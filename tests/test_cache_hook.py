"""Tests for the PlanckBot cache hook handler + the shell entrypoint.

Two layers:
- `handle()` unit tests on the pure-Python handler (most coverage here).
- One end-to-end subprocess test of `scripts/planckbot-hook.py` to confirm
  stdin/stdout plumbing works.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from planckbot.hooks.cache_hook import handle, _extract_text
from planckbot.tools.cache import ToolCacheStore


# --- handle() unit tests -------------------------------------------------


def test_unknown_event_returns_empty(conn):
    store = ToolCacheStore(conn)
    assert handle({"hook_event_name": "Bogus"}, store) == {}
    assert handle({}, store) == {}


def test_pretooluse_read_miss_passes_through(conn):
    store = ToolCacheStore(conn)
    result = handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "/does/not/exist.py"},
        "session_id": "s1",
    }, store)
    assert result == {}


def test_pretooluse_read_hit_denies_with_content(tmp_path, conn):
    p = tmp_path / "f.py"
    body = "def foo():\n    return 42"
    p.write_text(body)
    store = ToolCacheStore(conn)
    store.put(
        session_id="s1", tool_name="Read", cache_key=str(p),
        content=body, file_path=str(p),
        file_mtime_ns=p.stat().st_mtime_ns,
    )
    result = handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
        "session_id": "s1",
    }, store)
    out = result.get("hookSpecificOutput")
    assert out is not None
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "PlanckBot cache hit" in out["permissionDecisionReason"]
    assert body in out["additionalContext"]


def test_pretooluse_read_stale_returns_empty(tmp_path, conn):
    """If the file changed on disk since caching, lookup must miss."""
    p = tmp_path / "f.py"
    p.write_text("v1")
    store = ToolCacheStore(conn)
    store.put(
        session_id="s1", tool_name="Read", cache_key=str(p),
        content="v1", file_path=str(p),
        file_mtime_ns=p.stat().st_mtime_ns,
    )
    import time
    time.sleep(0.01)
    p.write_text("v2 — changed")
    result = handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
        "session_id": "s1",
    }, store)
    assert result == {}


def test_pretooluse_edit_marks_dirty(tmp_path, conn):
    p = tmp_path / "f.py"
    p.write_text("original")
    store = ToolCacheStore(conn)
    store.put(
        session_id="s1", tool_name="Read", cache_key=str(p),
        content="original", file_path=str(p),
        file_mtime_ns=p.stat().st_mtime_ns,
    )
    # Edit hook fires
    result = handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(p), "old_string": "a", "new_string": "b"},
        "session_id": "s1",
    }, store)
    assert result == {}
    # Subsequent Read on same path must now miss
    result = handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
        "session_id": "s1",
    }, store)
    assert result == {}


@pytest.mark.parametrize("tool", ["Write", "MultiEdit", "NotebookEdit"])
def test_pretooluse_other_writers_also_mark_dirty(tmp_path, conn, tool):
    p = tmp_path / "f.py"
    p.write_text("body")
    store = ToolCacheStore(conn)
    store.put(
        session_id="s1", tool_name="Read", cache_key=str(p),
        content="body", file_path=str(p),
        file_mtime_ns=p.stat().st_mtime_ns,
    )
    handle({
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(p)},
        "session_id": "s1",
    }, store)
    assert handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
        "session_id": "s1",
    }, store) == {}


def test_pretooluse_no_session_uses_fallback(tmp_path, conn):
    p = tmp_path / "f.py"
    p.write_text("x")
    store = ToolCacheStore(conn)
    # Put under the fallback session_id
    store.put(
        session_id="no-session", tool_name="Read", cache_key=str(p),
        content="x", file_path=str(p),
        file_mtime_ns=p.stat().st_mtime_ns,
    )
    result = handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
    }, store)
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_pretooluse_unsupported_tool_passes_through(conn):
    store = ToolCacheStore(conn)
    result = handle({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "session_id": "s1",
    }, store)
    assert result == {}


def test_posttooluse_read_caches(tmp_path, conn):
    p = tmp_path / "f.py"
    p.write_text("contents on disk")
    store = ToolCacheStore(conn)
    handle({
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
        "tool_response": "contents on disk",
        "session_id": "s1",
    }, store)
    hit = store.lookup(
        session_id="s1", tool_name="Read", cache_key=str(p),
        verify_mtime=True,
    )
    assert hit is not None
    assert hit.content == "contents on disk"
    assert hit.file_mtime_ns == p.stat().st_mtime_ns


def test_posttooluse_dict_response(tmp_path, conn):
    p = tmp_path / "f.py"
    p.write_text("text")
    store = ToolCacheStore(conn)
    handle({
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
        "tool_response": {"output": "text"},
        "session_id": "s1",
    }, store)
    hit = store.lookup(
        session_id="s1", tool_name="Read", cache_key=str(p),
        verify_mtime=True,
    )
    assert hit.content == "text"


def test_posttooluse_missing_file_skipped(conn):
    store = ToolCacheStore(conn)
    handle({
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "/nowhere/ghost.py"},
        "tool_response": "irrelevant",
        "session_id": "s1",
    }, store)
    assert store.count() == 0


def test_posttooluse_non_read_no_op(conn):
    store = ToolCacheStore(conn)
    handle({
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": "some output",
        "session_id": "s1",
    }, store)
    assert store.count() == 0


def test_posttooluse_empty_response_skipped(tmp_path, conn):
    p = tmp_path / "f.py"
    p.write_text("data")
    store = ToolCacheStore(conn)
    handle({
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(p)},
        "tool_response": "",
        "session_id": "s1",
    }, store)
    assert store.count() == 0


# --- extract_text helpers ------------------------------------------------


def test_extract_text_handles_variants():
    assert _extract_text(None) == ""
    assert _extract_text("hello") == "hello"
    assert _extract_text({"output": "out"}) == "out"
    assert _extract_text({"content": "c"}) == "c"
    assert _extract_text({"text": "t"}) == "t"
    assert _extract_text({"stdout": "s"}) == "s"
    assert _extract_text([{"text": "a"}, {"text": "b"}]) == "a\nb"
    assert _extract_text([{"text": "a"}, "raw"]) == "a\nraw"
    assert _extract_text(42) == ""  # int → empty


# --- subprocess smoke test on the real script ---------------------------


def test_script_passes_through_unknown_event(tmp_path):
    """Run the actual hook script with a JSON the handler doesn't act on."""
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "planckbot-hook.py"
    db = tmp_path / "hook.db"
    env = {
        **os.environ,
        "PLANCKBOT_DB": str(db),
        "PYTHONPATH": str(repo / "src"),
    }
    payload = json.dumps({"hook_event_name": "Bogus"})
    result = subprocess.run(
        [sys.executable, str(script)],
        input=payload, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_script_emits_deny_on_cache_hit(tmp_path):
    """Pre-populate cache via the handler, then invoke the script and
    confirm it emits the deny JSON to stdout."""
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "planckbot-hook.py"
    db = tmp_path / "hook.db"
    target = tmp_path / "watched.py"
    target.write_text("body")

    # Seed cache via library API in the same DB the script will read.
    from planckbot.db.engine import get_connection
    conn = get_connection(db)
    store = ToolCacheStore(conn)
    store.put(
        session_id="s-test", tool_name="Read", cache_key=str(target),
        content="cached body", file_path=str(target),
        file_mtime_ns=target.stat().st_mtime_ns,
    )
    conn.close()

    env = {
        **os.environ,
        "PLANCKBOT_DB": str(db),
        "PYTHONPATH": str(repo / "src"),
    }
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(target)},
        "session_id": "s-test",
    })
    result = subprocess.run(
        [sys.executable, str(script)],
        input=payload, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "cached body" in out["hookSpecificOutput"]["additionalContext"]


def test_script_disabled_env_passes_through(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "planckbot-hook.py"
    env = {**os.environ, "PLANCKBOT_HOOK_DISABLED": "1"}
    result = subprocess.run(
        [sys.executable, str(script)],
        input='{"hook_event_name": "PreToolUse"}',
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_script_invalid_json_passes_through(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "planckbot-hook.py"
    env = {**os.environ}
    result = subprocess.run(
        [sys.executable, str(script)],
        input="not json at all",
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == ""
