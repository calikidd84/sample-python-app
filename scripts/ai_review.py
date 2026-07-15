#!/usr/bin/env python3
"""
AI code review step for the CSE636 Week 2 lab.
Reads recently changed Python files and asks an OpenRouter model for a code review.
"""

import json
import os
import ssl
import subprocess
import urllib.request

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT


def get_changed_files():
    """Python files changed in the last commit; fall back to all tracked .py files."""
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    changed = (
        [f for f in diff.stdout.strip().split("\n") if f.endswith(".py")]
        if diff.returncode == 0
        else []
    )
    if changed:
        return changed

    listed = subprocess.run(
        ["git", "ls-files", "*.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [f for f in listed.stdout.strip().split("\n") if f.endswith(".py")]


def read_file(path):
    """Read file contents."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def review_code(filename, content):
    """Ask OpenRouter to review a single file using pure urllib."""
    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://jenkins.local",
        "X-Title": "Jenkins AI Code Review Pipeline",
    }

    data = {
        "model": "anthropic/claude-3.5-sonnet",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Please review the following Python file for correctness, "
                    f"style issues, and potential bugs. Be concise.\n\n"
                    f"Filename: {filename}\n\n"
                    f"```python\n{content}\n```"
                ),
            }
        ],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=60) as response:
            res_data = json.loads(response.read().decode("utf-8"))
        return res_data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error during AI code review api call: {e}"


def main():
    """Run AI review over changed files and write a report."""
    changed = get_changed_files()
    if not changed:
        print("No Python files found in the repo. Skipping AI review.")
        with open("ai_review_report.txt", "w", encoding="utf-8") as fh:
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

    with open("ai_review_report.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(report_lines))

    print("AI review complete. Report saved to ai_review_report.txt")


if __name__ == "__main__":
    main()
