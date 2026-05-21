"""Bulk extractor for `tool_use_id -> next_assistant_text` from Claude Code
JSONL transcripts. Used by techniques that need to score against what the
host LLM actually said next (split tools, dead-end prompts).

Differs from `cron/scanner.py :: find_response_after_tool_call` in that it
streams ALL tool_uses in a project in one pass instead of looking up one by
one — orders of magnitude faster when the bench needs thousands.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_references_for_project(
    claude_root: Path,
    slug: str,
) -> dict[str, str]:
    """Walk every JSONL in `claude_root/slug/` and build a mapping
    `tool_use_id -> next assistant text block`.

    The "next assistant text" is concatenated assistant blocks between the
    tool_result and either (a) the next user message that isn't a
    tool_result wrapper, or (b) the next tool_use. This is wider than
    `auto_label.py`'s single-block window (matches the Orquesta-report
    suggestion #1 — see session-2026-05-21 doc §5).
    """
    refs: dict[str, str] = {}
    proj_dir = claude_root / slug
    if not proj_dir.exists():
        return refs

    for jf in sorted(proj_dir.glob("*.jsonl")):
        try:
            entries = list(_iter_jsonl(jf))
        except OSError:
            continue
        _accumulate_refs_from_entries(entries, refs)
    return refs


def _iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _accumulate_refs_from_entries(entries: list[dict], refs: dict[str, str]):
    """Walk a single JSONL. After each tool_result block, capture
    subsequent assistant text until we hit the next user message (that
    isn't a tool_result wrapper) OR the next tool_use."""
    pending_ids: list[str] = []   # tool_use_ids awaiting their reference text
    buffer: list[str] = []        # assistant text accumulating after results

    def flush():
        text = "\n".join(buffer).strip()
        if text and pending_ids:
            for tid in pending_ids:
                refs[tid] = text
        pending_ids.clear()
        buffer.clear()

    for entry in entries:
        role = entry.get("type") or (entry.get("message") or {}).get("role")
        msg = entry.get("message") or {}
        content = msg.get("content")
        if role == "user":
            if isinstance(content, list):
                has_tool_result = any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
                if not has_tool_result:
                    flush()
            else:
                flush()
        elif role == "assistant":
            if not isinstance(content, list):
                continue
            saw_tool_use = False
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                btype = blk.get("type")
                if btype == "tool_use":
                    saw_tool_use = True
                elif btype == "text":
                    txt = (blk.get("text") or "").strip()
                    if txt:
                        buffer.append(txt)
            if saw_tool_use:
                flush()
        # tool_result themselves arrive in user-role messages
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    tid = blk.get("tool_use_id")
                    if tid:
                        pending_ids.append(tid)
    flush()
