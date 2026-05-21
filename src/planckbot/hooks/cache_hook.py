"""PreToolUse/PostToolUse hook handler that powers the A.cache_deny_read
optimization. Pure-Python; the shell entrypoint at `scripts/planckbot-hook.py`
is a thin wrapper that reads stdin and writes stdout.

Behavior:
  PreToolUse + Read              → cache lookup; on fresh hit returns
                                    `permissionDecision: deny` with the
                                    cached content in `additionalContext`
                                    so Claude sees the file content
                                    without paying for the real Read.
  PreToolUse + Edit/Write/...    → marks the cache dirty for that path so
                                    future Reads in the same session miss.
                                    Never blocks.
  PostToolUse + Read             → stores the result in the cache with
                                    file_mtime_ns captured for staleness
                                    checks.
  Anything else                  → pass-through ({}).

Always returns a dict (possibly empty). Callers convert empty {} to "no
output" so Claude Code proceeds normally.
"""

from __future__ import annotations

import os
from typing import Any

from planckbot.tools.cache import ToolCacheStore


_INVALIDATING_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def handle(input_json: dict, store: ToolCacheStore) -> dict:
    """Top-level dispatch on hook_event_name."""
    event = input_json.get("hook_event_name")
    if event == "PreToolUse":
        return _pretooluse(input_json, store)
    if event == "PostToolUse":
        return _posttooluse(input_json, store)
    return {}


# --- PreToolUse ---------------------------------------------------------


def _pretooluse(input_json: dict, store: ToolCacheStore) -> dict:
    tool_name = input_json.get("tool_name")
    tool_input = input_json.get("tool_input") or {}
    session_id = input_json.get("session_id") or "no-session"

    # Mark cache dirty when a write-style tool touches a path. Never blocks.
    if tool_name in _INVALIDATING_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(path, str):
            store.mark_dirty(session_id=session_id, file_path=path)
        return {}

    if tool_name != "Read":
        return {}

    path = tool_input.get("file_path")
    if not isinstance(path, str):
        return {}

    hit = store.lookup(
        session_id=session_id,
        tool_name="Read",
        cache_key=path,
        verify_mtime=True,
    )
    if hit is None:
        return {}

    store.record_hit(hit.id)
    tokens_saved = hit.content_tokens or 0
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PlanckBot cache hit (~{tokens_saved} tokens saved)"
            ),
            "additionalContext": (
                f"[PlanckBot cache] Read of {path} served from cache "
                f"(mtime unchanged since first read). File content:\n\n"
                f"{hit.content}"
            ),
        }
    }


# --- PostToolUse --------------------------------------------------------


def _posttooluse(input_json: dict, store: ToolCacheStore) -> dict:
    tool_name = input_json.get("tool_name")
    if tool_name != "Read":
        return {}

    tool_input = input_json.get("tool_input") or {}
    path = tool_input.get("file_path")
    if not isinstance(path, str):
        return {}

    session_id = input_json.get("session_id") or "no-session"

    # Claude Code's exact field for the tool result text varies between
    # versions; try the documented names in order.
    raw_response = (
        input_json.get("tool_response")
        or input_json.get("tool_result")
        or input_json.get("response")
    )
    content = _extract_text(raw_response)
    if not content:
        return {}

    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        # File missing / unreadable — don't cache something we can't verify.
        return {}

    store.put(
        session_id=session_id,
        tool_name="Read",
        cache_key=path,
        content=content,
        file_path=path,
        file_mtime_ns=mtime,
    )
    return {}


# --- helpers ------------------------------------------------------------


def _extract_text(response: Any) -> str:
    """Tool responses can be string, dict, or list-of-blocks. Flatten."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for k in ("output", "content", "text", "stdout"):
            v = response.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, list):
                return _flatten_blocks(v)
        return ""
    if isinstance(response, list):
        return _flatten_blocks(response)
    return ""


def _flatten_blocks(blocks: list) -> str:
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, str):
            parts.append(b)
        elif isinstance(b, dict):
            if "text" in b:
                parts.append(str(b["text"]))
    return "\n".join(parts)
