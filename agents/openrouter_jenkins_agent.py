#!/usr/bin/env python3
"""Natural-language Jenkins agent backed by OpenRouter function calling."""

import argparse
import base64
import json
import os
import ssl
import urllib.request
from urllib.error import HTTPError, URLError

JENKINS_URL = os.environ.get("JENKINS_URL", "http://localhost:8080")
JENKINS_USER = os.environ.get("JENKINS_USER", "")
JENKINS_TOKEN = os.environ.get("JENKINS_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
MAX_TOOL_ROUNDS = 10

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

SYSTEM_PROMPT = (
    "You are a Jenkins CI assistant. You have access to tools that interact "
    "with a Jenkins server. Use the tools to answer the user's questions. "
    "When you have enough information, provide a clear, concise final answer."
)

JENKINS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_jobs",
            "description": "List all Jenkins job names.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_build_status",
            "description": "Get the latest build status for a Jenkins job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "The Jenkins job name, e.g. 'ai-review-demo'",
                    }
                },
                "required": ["job_name"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Jenkins API helpers
# ---------------------------------------------------------------------------

def _build_auth_header():
    if not JENKINS_USER or not JENKINS_TOKEN:
        raise RuntimeError(
            "JENKINS_USER and JENKINS_TOKEN environment variables must be set. "
            "Generate an API token at: http://localhost:8080/user/<your-user>/configure"
        )
    credentials = base64.b64encode(
        f"{JENKINS_USER}:{JENKINS_TOKEN}".encode("utf-8")
    ).decode("ascii")
    return {"Authorization": f"Basic {credentials}"}


def _jenkins_get(path):
    """Make an authenticated GET request to Jenkins and return parsed JSON."""
    url = f"{JENKINS_URL.rstrip('/')}{path}"
    headers = {"Accept": "application/json", **_build_auth_header()}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, context=_ssl_ctx, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError(
                f"Jenkins returned 401 Unauthorized for {url}. "
                "Check that JENKINS_USER and JENKINS_TOKEN are correct."
            ) from exc
        if exc.code == 404:
            return {"error": "Resource not found."}
        raise RuntimeError(f"Jenkins request failed with status {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Jenkins at {url}: {exc}") from exc


def list_all_jobs():
    """Return a list of Jenkins job names."""
    data = _jenkins_get("/api/json?tree=jobs[name]")
    if "error" in data:
        return data
    return [job["name"] for job in data.get("jobs", [])]


def get_jenkins_build_status(job_name):
    """Fetch the latest Jenkins build status for a job."""
    data = _jenkins_get(f"/job/{job_name}/lastBuild/api/json")
    if "error" in data:
        return {"error": f"Job '{job_name}' not found."}
    return {
        "result": data.get("result", "IN_PROGRESS"),
        "number": data.get("number", "?"),
        "url": data.get("url", ""),
    }


# ---------------------------------------------------------------------------
# Tool execution dispatcher
# ---------------------------------------------------------------------------

def execute_tool(tool_name, arguments):
    """Execute a Jenkins tool call and return the result as a string."""
    if tool_name == "list_jobs":
        result = list_all_jobs()
        if isinstance(result, dict) and "error" in result:
            return result["error"]
        return f"Jenkins jobs: {', '.join(result)}"

    if tool_name == "get_build_status":
        job_name = arguments.get("job_name", "")
        result = get_jenkins_build_status(job_name)
        if "error" in result:
            return result["error"]
        return (
            f"Job '{job_name}': build #{result['number']} — {result['result']}"
        )

    return f"Unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# OpenRouter API
# ---------------------------------------------------------------------------

def _openrouter_chat(messages, tools=None, model=OPENROUTER_MODEL, api_key=OPENROUTER_API_KEY):
    """Send a chat completion request to OpenRouter and return the response dict."""
    if not api_key:
        raise RuntimeError("OpenRouter API key is not configured.")

    payload = {
        "model": model,
        "max_tokens": 1024,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://jenkins.local",
            "X-Title": "Jenkins AI Agent",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, context=_ssl_ctx, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(query, model=OPENROUTER_MODEL, api_key=OPENROUTER_API_KEY):
    """Run the natural-language agent loop and return the final text answer."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    for _ in range(MAX_TOOL_ROUNDS):
        data = _openrouter_chat(messages, tools=JENKINS_TOOLS, model=model, api_key=api_key)
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        finish = choice.get("finish_reason", "")

        # If the model produced a final text answer (no tool calls), return it
        if not msg.get("tool_calls"):
            return msg.get("content", "")

        # Append the assistant message (with tool_calls) to the conversation
        messages.append(msg)

        # Execute each tool call and append results
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                arguments = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}
            result = execute_tool(tool_name, arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })

    return "Agent reached maximum tool-call rounds without a final answer."


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Ask a natural-language question about Jenkins jobs."
    )
    parser.add_argument("query", help="Natural language query, e.g. 'List all Jenkins jobs'")
    parser.add_argument("--model", default=OPENROUTER_MODEL, help="OpenRouter model name")
    parser.add_argument("--api-key", default=OPENROUTER_API_KEY, help="OpenRouter API key")
    args = parser.parse_args()

    answer = run_agent(args.query, model=args.model, api_key=args.api_key)
    print(answer)


if __name__ == "__main__":
    main()
