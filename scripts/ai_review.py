#!/usr/bin/env python3
"""
AI code review step for the CSE636 Week 2 lab.
Reads recently changed Python files and asks an OpenRouter model for a code review.
"""
import os
import ssl
import subprocess
import certifi
import httpx  # Used directly to build the client
from openai import OpenAI  # OpenRouter uses the OpenAI-compatible client structure

# Read the newly configured OpenRouter environment variable
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

# TLS trust behind an inspecting proxy (e.g. Zscaler). The SDK verifies
# via httpx against certifi's bundle — NOT the OS store. Build the SSL context
# explicitly from a bundle that includes the corporate root CA (SSL_CERT_FILE /
# REQUESTS_CA_BUNDLE, else certifi's default), then clear VERIFY_X509_STRICT:
# Python 3.13 turns on strict RFC 5280 verification by default, and many corporate
# CAs (Zscaler) ship a CA cert whose basicConstraints isn't marked critical, which
# strict mode rejects ("Basic Constraints of CA cert not marked critical"). We
# still require a valid cert, full chain, and hostname match — only the
# over-strict RFC check is relaxed.
_ca_bundle = (os.environ.get("SSL_CERT_FILE")
              or os.environ.get("REQUESTS_CA_BUNDLE")
              or certifi.where())
_ssl_ctx = ssl.create_default_context(cafile=_ca_bundle)
_ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

# Initialize the OpenAI client with OpenRouter's endpoint and custom headers
client = OpenAI(
    base_url="https://openrouter.ai",
    api_key=OPENROUTER_API_KEY,
    http_client=httpx.Client(verify=_ssl_ctx),
    default_headers={
        "HTTP-Referer": "https://jenkins.local", # Optional, for OpenRouter analytics
        "X-Title": "Jenkins AI Code Review Pipeline" # Optional, for OpenRouter analytics
    }
)

def get_changed_files():
    """Python files changed in the last commit; fall back to all tracked .py files.

    The diff is empty when the last commit touched no .py file, the repo has a
    single commit (no HEAD~1), or Jenkins did a shallow clone. In those cases we
    review every tracked .py file so the stage always produces output.
    """
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        capture_output=True, text=True
    )
    changed = (
        [f for f in diff.stdout.strip().split("\n") if f.endswith(".py")]
        if diff.returncode == 0 else []
    )
    if changed:
        return changed

    # Fallback: review all tracked Python files.
    listed = subprocess.run(
        ["git", "ls-files", "*.py"],
        capture_output=True, text=True
    )
    return [f for f in listed.stdout.strip().split("\n") if f.endswith(".py")]

def read_file(path):
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError:
        return None

def review_code(filename, content):
    """Ask OpenRouter to review a single file."""
    # Use OpenAI's chat completion format and specify an OpenRouter model string
    response = client.chat.completions.create(
        model="anthropic/claude-3.5-sonnet", # OpenRouter model syntax
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Please review the following Python file for correctness, "
                    f"style issues, and potential bugs. Be concise.\n\n"
                    f"Filename: {filename}\n\n"
                    f"```python\n{content}\n```"
                )
            }
        ]
    )
    return response.choices[0].message.content

def main():
    changed = get_changed_files()
    if not changed:
        print("No Python files found in the repo. Skipping AI review.")
        with open("ai_review_report.txt", "w") as fh:
            fh.write("No Python files found in the repo.\n")
        return

    report_lines = ["# AI Code Review Report\n"]
    for filepath in changed:
        content = read_file(filepath)
        if content is None:
            continue
        print(f"Reviewing {filepath}...")
        review = review_code(filepath, content)
        report_lines.append(f"\n## {filepath}\n\n{review}\n")
        print(f"Review for {filepath}:\n{review}\n")

    with open("ai_review_report.txt", "w") as fh:
        fh.write("\n".join(report_lines))
    print("AI review complete. Report saved to ai_review_report.txt")

if __name__ == "__main__":
    main()
