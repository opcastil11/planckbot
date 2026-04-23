"""Layer D MCP server — exposes synthesized tools to Claude Code.

Runs as a separate MCP stdio server (entry point `planckbot-synth`, see
`pyproject.toml`). On startup it loads every `status='active'` row from the
`synthesized_tools` table and registers it with the MCP runtime. When it
receives SIGHUP it re-reads the table and sends `tools/list_changed` so
Claude Code re-discovers the catalog without a restart.

Register with Claude Code (in `~/.claude.json`):

    "planckbot-synth": {
        "command": "/abs/path/.venv/bin/planckbot-synth"
    }

The server writes its pid to `/tmp/planckbot-synth.pid` so the
`synthesize_tool` meta-tool can SIGHUP the running instance from inside the
main planckbot process.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from planckbot.config import config
from planckbot.db.engine import get_connection
from planckbot.db.models import SynthesizedTool
from planckbot.synth.meta import (
    DEFAULT_SYNTH_PID_FILE,
    SynthesizedToolStore,
    load_function_from_code,
)


PID_FILE = DEFAULT_SYNTH_PID_FILE


def _write_pid(pid_file: Path) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


def _clear_pid(pid_file: Path) -> None:
    try:
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass


def _tool_from_row(t: SynthesizedTool) -> Tool:
    """Convert a DB row into an MCP Tool advertisement.

    The DB stores `input_schema` as a flat `{param: type}` map (simple form).
    MCP expects a JSON Schema object, so we wrap it.
    """
    properties: dict[str, Any] = {}
    for k, v in (t.input_schema or {}).items():
        if isinstance(v, dict):
            properties[k] = v
        else:
            properties[k] = {"type": str(v or "string")}
    schema = {
        "type": "object",
        "properties": properties,
    }
    return Tool(
        name=t.name,
        description=t.description or f"Synthesized tool: {t.name}",
        inputSchema=schema,
    )


def _dispatch(tool: SynthesizedTool, arguments: dict) -> str:
    """Load the tool's code and call it with `arguments`. Returns text."""
    fn = load_function_from_code(tool.code, tool.name)
    result = fn(**(arguments or {}))
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except TypeError:
        return repr(result)


async def run_server(server_name: str = "planckbot-synth") -> None:
    conn = get_connection(config.db_path)
    store = SynthesizedToolStore(conn)

    # Mutable cache so SIGHUP can swap it out without rebuilding the server.
    active_cache: dict[str, SynthesizedTool] = {
        t.name: t for t in store.list_active()
    }

    server = Server(server_name)

    def _reload() -> None:
        nonlocal active_cache
        active_cache = {t.name: t for t in store.list_active()}
        print(
            f"[synth] reloaded — {len(active_cache)} active tool(s): "
            f"{sorted(active_cache)}",
            file=sys.stderr,
            flush=True,
        )

    # Wire SIGHUP to trigger a reload. We can't send MCP notifications from
    # a signal handler (it's async-unsafe), so the next list_tools() call
    # from the client naturally picks up the new catalog.
    def _on_sighup(signum, frame):
        _reload()

    signal.signal(signal.SIGHUP, _on_sighup)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [_tool_from_row(t) for t in active_cache.values()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):
        t = active_cache.get(name)
        if t is None:
            # Refresh once in case we missed a SIGHUP.
            _reload()
            t = active_cache.get(name)
        if t is None:
            return [TextContent(
                type="text",
                text=f"synthesized tool not found or not active: {name}",
            )]
        try:
            text = _dispatch(t, arguments or {})
        except Exception as e:
            text = f"synthesized tool {name!r} raised {type(e).__name__}: {e}"
        return [TextContent(type="text", text=text)]

    _write_pid(PID_FILE)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        _clear_pid(PID_FILE)


def main() -> None:
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
