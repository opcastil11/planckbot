"""Tiny stdio MCP server used by the proxy integration test.

Exposes one tool, `echo_lines`, that returns a verbose multi-line string
mixing relevant and noise lines — exactly the shape PlanckBot is built to
filter. Launched as a subprocess by the proxy under test.
"""

import asyncio

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


async def main() -> None:
    server = Server("stub-upstream")

    @server.list_tools()
    async def _list_tools():
        return [
            Tool(
                name="echo_lines",
                description="Return a verbose echo of the given query.",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):
        if name != "echo_lines":
            raise ValueError(f"unknown tool: {name}")
        q = arguments.get("query", "")
        body = (
            f"src/{q}/core.py\n"
            f"src/{q}/handlers.py\n"
            f"node_modules/@corp/{q}/index.js\n"
            f"dist/{q}.min.js"
        )
        return [TextContent(type="text", text=body)]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
