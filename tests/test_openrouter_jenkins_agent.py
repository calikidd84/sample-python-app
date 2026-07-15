import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import openrouter_jenkins_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        if isinstance(self._payload, str):
            return self._payload.encode("utf-8")
        return self._payload


# ---------------------------------------------------------------------------
# list_all_jobs
# ---------------------------------------------------------------------------

def test_list_all_jobs_returns_names(monkeypatch):
    monkeypatch.setenv("JENKINS_USER", "admin")
    monkeypatch.setenv("JENKINS_TOKEN", "fake-token")
    monkeypatch.setattr(openrouter_jenkins_agent, "JENKINS_USER", "admin")
    monkeypatch.setattr(openrouter_jenkins_agent, "JENKINS_TOKEN", "fake-token")
    payload = json.dumps({"jobs": [{"name": "alpha"}, {"name": "beta"}]})
    monkeypatch.setattr(
        openrouter_jenkins_agent.urllib.request,
        "urlopen",
        lambda req, context=None, timeout=None: FakeResponse(payload),
    )
    jobs = openrouter_jenkins_agent.list_all_jobs()
    assert jobs == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# get_jenkins_build_status
# ---------------------------------------------------------------------------

def test_get_jenkins_build_status_parses_payload(monkeypatch):
    monkeypatch.setattr(openrouter_jenkins_agent, "JENKINS_USER", "admin")
    monkeypatch.setattr(openrouter_jenkins_agent, "JENKINS_TOKEN", "fake-token")
    payload = json.dumps(
        {"result": "SUCCESS", "number": 7, "url": "https://jenkins.example/job/demo/7/"}
    )
    monkeypatch.setattr(
        openrouter_jenkins_agent.urllib.request,
        "urlopen",
        lambda req, context=None, timeout=None: FakeResponse(payload),
    )
    status = openrouter_jenkins_agent.get_jenkins_build_status("demo")
    assert status["result"] == "SUCCESS"
    assert status["number"] == 7
    assert status["url"].endswith("/7/")


# ---------------------------------------------------------------------------
# execute_tool
# ---------------------------------------------------------------------------

def test_execute_tool_list_jobs(monkeypatch):
    monkeypatch.setattr(
        openrouter_jenkins_agent,
        "list_all_jobs",
        lambda: ["job-a", "job-b"],
    )
    result = openrouter_jenkins_agent.execute_tool("list_jobs", {})
    assert "job-a" in result
    assert "job-b" in result


def test_execute_tool_get_build_status(monkeypatch):
    monkeypatch.setattr(
        openrouter_jenkins_agent,
        "get_jenkins_build_status",
        lambda name: {"result": "FAILURE", "number": 3, "url": "http://x"},
    )
    result = openrouter_jenkins_agent.execute_tool("get_build_status", {"job_name": "demo"})
    assert "FAILURE" in result
    assert "3" in result


def test_execute_tool_unknown():
    result = openrouter_jenkins_agent.execute_tool("no_such_tool", {})
    assert "Unknown tool" in result


# ---------------------------------------------------------------------------
# run_agent (end-to-end with mocked OpenRouter)
# ---------------------------------------------------------------------------

def test_run_agent_returns_final_answer(monkeypatch):
    """When OpenRouter returns content with no tool_calls, the agent returns it directly."""
    def fake_chat(messages, tools=None, model="m", api_key="k"):
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "All clear!"},
                }
            ]
        }

    monkeypatch.setattr(openrouter_jenkins_agent, "_openrouter_chat", fake_chat)
    answer = openrouter_jenkins_agent.run_agent("Is everything OK?")
    assert answer == "All clear!"


def test_run_agent_executes_tool_then_returns(monkeypatch):
    """Agent calls list_jobs tool, then returns the final answer."""
    call_count = {"n": 0}

    def fake_chat(messages, tools=None, model="m", api_key="k"):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: request a tool
            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "list_jobs",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        # Second call: final answer
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "You have 2 jobs."},
                }
            ]
        }

    monkeypatch.setattr(openrouter_jenkins_agent, "_openrouter_chat", fake_chat)
    monkeypatch.setattr(
        openrouter_jenkins_agent,
        "execute_tool",
        lambda name, args: "Jenkins jobs: a, b",
    )

    answer = openrouter_jenkins_agent.run_agent("List jobs")
    assert answer == "You have 2 jobs."
    assert call_count["n"] == 2
