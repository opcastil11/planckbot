"""Claude Code conversation-log scanner.

Closes the last manual piece of the auto-label loop: instead of the user
copy-pasting their last Claude message into a reference file, this module
reads Claude Code's per-project JSONL transcripts and extracts recent
assistant text blocks so the `autolabel` job has something to match against.

A JSONL line looks roughly like:
    {"type": "assistant", "timestamp": "...", "message": {"content": [
        {"type": "thinking", ...},
        {"type": "text", "text": "..."},
        {"type": "tool_use", ...},
    ]}}

We only keep `text` blocks (tool_use/thinking are not the LLM citing tool
output; they ARE the tool invocation and the internal deliberation).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _slugify_cwd(cwd: str | Path) -> str:
    """Convert an absolute path into the slug Claude Code uses for project dirs.

    Example: /home/kai/Escritorio/PROGRAMACION/planckbot
    becomes -home-kai-Escritorio-PROGRAMACION-planckbot
    """
    return str(cwd).replace("/", "-")


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        # Claude Code timestamps can be ISO 8601 with or without 'Z'
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_assistant_texts(
    jsonl_path: Path,
    *,
    since: datetime | None = None,
) -> list[tuple[datetime, str]]:
    """Walk a JSONL file and yield (timestamp, text) for every assistant text
    block newer than `since`.

    Returned list is in file order (i.e. chronological).
    """
    out: list[tuple[datetime, str]] = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "assistant":
                continue
            ts = _parse_ts(msg.get("timestamp", ""))
            if ts is None:
                continue
            if since is not None and ts < since:
                continue
            content = msg.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "text":
                    continue
                text = (block.get("text") or "").strip()
                if text:
                    out.append((ts, text))
    return out


def scan_project_conversations(
    project_slug: str,
    *,
    claude_root: Path | None = None,
    since: datetime | None = None,
    max_messages: int | None = None,
) -> list[tuple[datetime, str]]:
    """Collect recent assistant texts across all JSONL files of a project."""
    root = (claude_root or Path.home() / ".claude" / "projects") / project_slug
    if not root.exists():
        return []

    combined: list[tuple[datetime, str]] = []
    for jsonl in sorted(root.glob("*.jsonl")):
        combined.extend(extract_assistant_texts(jsonl, since=since))

    combined.sort(key=lambda t: t[0])  # ensure chronological across files
    if max_messages is not None and len(combined) > max_messages:
        combined = combined[-max_messages:]
    return combined


def write_reference_file(
    output_path: Path,
    entries: list[tuple[datetime, str]],
) -> int:
    """Dump assistant texts as a single reference file. Returns chars written."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n---\n\n".join(text for _ts, text in entries)
    output_path.write_text(body)
    return len(body)


def scan_job(ctx) -> str:
    """cron-engine-compatible job. Params:

        output_path (str)      — where to write the combined reference file
        project_slug (str)     — Claude Code project dir slug. Optional:
                                 defaults to the slugified cwd.
        max_messages (int)     — cap on included messages (default 30)
        lookback_hours (int)   — only include messages newer than N hours
                                 (default 24)
        claude_root (str)      — override for tests (default ~/.claude/projects)
    """
    params = ctx.params
    output_path = params.get("output_path")
    if not output_path:
        raise ValueError("conversation_scanner requires params.output_path")

    project_slug = params.get("project_slug") or _slugify_cwd(Path.cwd())
    max_messages = int(params.get("max_messages", 30))
    lookback_hours = int(params.get("lookback_hours", 24))
    claude_root = params.get("claude_root")
    claude_root_path = Path(claude_root) if claude_root else None

    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    entries = scan_project_conversations(
        project_slug,
        claude_root=claude_root_path,
        since=since,
        max_messages=max_messages,
    )
    if not entries:
        return (
            f"project={project_slug} lookback={lookback_hours}h "
            "no assistant messages in window"
        )

    chars = write_reference_file(Path(output_path), entries)
    return (
        f"project={project_slug} messages={len(entries)} "
        f"chars={chars} → {output_path}"
    )
