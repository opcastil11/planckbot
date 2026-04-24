"""Pre-flight: spawn planckbot-mcp wrapping @modelcontextprotocol/server-filesystem,
connect as an MCP client, list tools, call one, and confirm a triple landed in
the DB. This is the exact shape Claude Code will use.

Run:
    NODE=/path/to/node/bin/npx python scripts/mcp_preflight.py [target_path]
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


DEFAULT_PATH = str(Path(__file__).resolve().parent.parent)


async def main(target_path: str, npx: str) -> int:
    planckbot_mcp = (
        Path(__file__).resolve().parent.parent / ".venv" / "bin" / "planckbot-mcp"
    )
    if not planckbot_mcp.exists():
        print(f"[preflight] missing binary: {planckbot_mcp}", file=sys.stderr)
        return 2

    params = StdioServerParameters(
        command=str(planckbot_mcp),
        args=[
            "--mode", "observe",
            "--name", "planckbot-preflight",
            "--",
            npx, "-y", "@modelcontextprotocol/server-filesystem", target_path,
        ],
    )

    print(f"[preflight] planckbot-mcp → npx @modelcontextprotocol/server-filesystem {target_path}")
    print(f"[preflight] connecting…")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            tools = await client.list_tools()
            print(f"[preflight] upstream advertised {len(tools.tools)} tools:")
            for t in tools.tools:
                print(f"             - {t.name}")

            # Pick list_directory if available, otherwise the first tool.
            tool_name = None
            tool_args: dict = {}
            for t in tools.tools:
                if t.name == "list_directory":
                    tool_name = "list_directory"
                    tool_args = {"path": target_path}
                    break
            if tool_name is None and tools.tools:
                tool_name = tools.tools[0].name

            if not tool_name:
                print("[preflight] no tools to exercise", file=sys.stderr)
                return 3

            print(f"[preflight] calling {tool_name}({tool_args})…")
            result = await client.call_tool(tool_name, tool_args)
            first_text = ""
            for block in result.content:
                if hasattr(block, "text"):
                    first_text = block.text[:240]
                    break
            print(f"[preflight] got {len(result.content)} content block(s)")
            if first_text:
                print(f"[preflight] first block (truncated): {first_text!r}")

    # Verify the triple landed in the DB
    db_path = Path(os.environ.get("PLANCK_DATA_DIR", "data")) / "planckbot.db"
    if not db_path.exists():
        print(f"[preflight] WARN: DB not at {db_path}", file=sys.stderr)
        return 0
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT tool_name, source, created_at FROM triples "
        "WHERE source LIKE 'proxy:%' "
        "ORDER BY created_at DESC LIMIT 5"
    ))
    conn.close()
    if not rows:
        print("[preflight] WARN: no proxy-sourced triples found in DB")
    else:
        print(f"[preflight] ✓ {len(rows)} recent proxy triple(s) in DB:")
        for r in rows:
            print(f"             {r['created_at']}  {r['tool_name']}  {r['source']}")
    return 0


def _find_npx() -> str:
    """Resolve npx via NPX env var, then PATH. We avoid hard-coding a path."""
    explicit = os.environ.get("NPX")
    if explicit:
        return explicit
    from shutil import which
    found = which("npx")
    if found:
        return found
    print(
        "[preflight] npx not found — set NPX=/path/to/npx or add it to $PATH",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    rc = asyncio.run(main(path, _find_npx()))
    sys.exit(rc)
