#!/usr/bin/env python3
"""
Minimal MCP server that exposes Jenkins build status to an AI agent.
CSE636 Week 2 Lab — Part 2.

Install: pip install mcp requests
"""
import os
import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

JENKINS_URL = os.environ.get("JENKINS_URL", "http://localhost:8080")
JENKINS_USER = os.environ.get("JENKINS_USER", "admin")
JENKINS_TOKEN = os.environ.get("JENKINS_TOKEN", "")

app = Server("cse636-jenkins-mcp")

@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="get_build_status",
            description=(
                "Returns the status and number of the most recent Jenkins build "
                "for a given job. Use this to check if a CI pipeline is currently "
                "passing or failing before making code changes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "The Jenkins job name, e.g. 'ai-review-demo'"
                    }
                },
                "required": ["job_name"]
            }
        ),
        Tool(
            name="list_jobs",
            description="Returns a list of all Jenkins job names.",
            inputSchema={"type": "object", "properties": {}}
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    auth = (JENKINS_USER, JENKINS_TOKEN) if JENKINS_TOKEN else None

    if name == "list_jobs":
        resp = requests.get(
            f"{JENKINS_URL}/api/json?tree=jobs[name]",
            auth=auth, timeout=10
        )
        jobs = [j["name"] for j in resp.json().get("jobs", [])]
        return [TextContent(type="text", text=f"Jenkins jobs: {', '.join(jobs)}")]

    if name == "get_build_status":
        job = arguments["job_name"]
        resp = requests.get(
            f"{JENKINS_URL}/job/{job}/lastBuild/api/json",
            auth=auth, timeout=10
        )
        if resp.status_code == 404:
            return [TextContent(type="text", text=f"Job '{job}' not found.")]
        data = resp.json()
        result = data.get("result", "IN_PROGRESS")
        number = data.get("number", "?")
        return [TextContent(
            type="text",
            text=f"Job '{job}': build #{number} — {result}"
        )]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]

if __name__ == "__main__":
    import asyncio
    from mcp.server import NotificationOptions
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="cse636-jenkins-mcp",
                    server_version="1.0.0",
                    capabilities=app.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={}
                    )
                )
            )

    asyncio.run(main())
