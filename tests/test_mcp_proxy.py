"""End-to-end test for the MCP stdio proxy.

This test spawns:
    client -> planckbot-mcp proxy -> stub upstream MCP server

It then:
    1. Lists tools via the client; asserts `echo_lines` is advertised
    2. Calls `echo_lines(query="auth")` via the client
    3. Asserts the text round-trips unchanged (observe mode)
    4. Confirms the proxy recorded a triple in a fresh SQLite file

Heavy test — spins up two subprocesses. Skipped if mcp SDK isn't importable.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import pytest


try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError:  # pragma: no cover
    pytest.skip("mcp SDK not installed", allow_module_level=True)


STUB_PATH = Path(__file__).parent / "fixtures" / "stub_mcp_server.py"


@pytest.mark.asyncio
async def test_proxy_round_trip_observe_mode(tmp_path):
    # Separate DB so the test doesn't collide with the real one.
    env = {**os.environ, "PLANCK_DATA_DIR": str(tmp_path)}

    proxy_args = [
        sys.executable, "-m", "planckbot.proxy.mcp_server",
        "--upstream-command", sys.executable,
        "--upstream-args", str(STUB_PATH),
        "--mode", "observe",
        "--name", "planckbot-test",
    ]

    params = StdioServerParameters(
        command=proxy_args[0], args=proxy_args[1:], env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            # 1) list_tools round-trips
            tools = await client.list_tools()
            names = [t.name for t in tools.tools]
            assert "echo_lines" in names

            # 2) call_tool round-trips
            result = await client.call_tool("echo_lines", {"query": "auth"})
            text_blocks = [b.text for b in result.content if hasattr(b, "text")]
            combined = "\n".join(text_blocks)
            assert "src/auth/core.py" in combined
            assert "node_modules/@corp/auth/index.js" in combined

    # 3) Triple landed in the test DB (observe mode: raw_output preserved)
    db_path = tmp_path / "planckbot.db"
    # Give the subprocess a moment to finish its final write + exit.
    for _ in range(20):
        if db_path.exists():
            break
        await asyncio.sleep(0.1)
    assert db_path.exists(), "proxy did not write the shared DB"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT tool_name, source, input_data, output_data FROM triples"
    ))
    conn.close()

    assert len(rows) == 1, f"expected 1 triple, got {len(rows)}"
    r = rows[0]
    assert r["tool_name"] == "echo_lines"
    assert r["source"] == "proxy:observe"
    assert "auth" in r["input_data"]
    assert "src/auth/core.py" in r["output_data"]
