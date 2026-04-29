"""Claude Code JSONL → triples.

Walks `~/.claude/projects/<slug>/*.jsonl`, pairs every `tool_use` block with
its matching `tool_result` (via `tool_use_id`), and yields one TripleRecord
per pair. Source name is `claude_code:jsonl`.

Why this exists: the planckbot-fs MCP proxy only sees calls that Claude Code
routes through `mcp__planckbot-fs__*`. Native tools (`Read`, `Edit`, `Write`,
`Bash`, `ToolSearch`, …) bypass it entirely. But Claude Code logs every
tool_use+tool_result to its per-project JSONL, so the data is on disk — just
not ingested. This source closes that gap.

Idempotency: dedup by the upstream `tool_use_id` (e.g.
`toolu_01Kzbh5dSQtVLHg12r2y9zoF`). On `ingest()` we pre-load every existing
tool_use_id from prior `claude_code:jsonl` rows and skip re-inserts.

Timestamps: the Claude Code wrapper carries an ISO timestamp on every entry.
We forward it as the triple's `created_at` so backfills don't show up as
"just now" in the dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Optional

from planckbot.cron.scanner import _slugify_cwd
from planckbot.ingest.base import TripleRecord, TripleSource
from planckbot.tools.triples import TriplesStore


class ClaudeCodeJsonlSource(TripleSource):
    name = "claude_code:jsonl"

    # Tool-name prefixes whose calls are already captured by another
    # source (the proxy MCP) — re-ingesting them from JSONL would create
    # a duplicate row with a different tool_name (`edit_file` from the
    # proxy vs. `mcp__planckbot-fs__edit_file` here). Skip by default.
    DEFAULT_SKIP_PREFIXES: tuple[str, ...] = ("mcp__planckbot-fs__",)

    def __init__(
        self,
        project_path: Path | str,
        *,
        claude_root: Path | str | None = None,
        skip_errors: bool = True,
        skip_prefixes: tuple[str, ...] | None = None,
    ):
        """
        Args:
            project_path: absolute path of the watched repo. Used to derive
                the Claude Code slug (`/a/b/c` → `-a-b-c`).
            claude_root: override for `~/.claude/projects` (tests).
            skip_errors: drop tool calls whose result has `is_error=True`.
                Same convention as OrquestaSource. Set False to keep them.
            skip_prefixes: tool-name prefixes to drop. Defaults to skipping
                the planckbot-fs MCP namespace because the proxy already
                stores those calls (under the un-namespaced tool name).
                Pass `()` to keep everything.
        """
        self.project_path = Path(project_path)
        self.claude_root = (
            Path(claude_root) if claude_root
            else Path.home() / ".claude" / "projects"
        )
        self.skip_errors = skip_errors
        self.skip_prefixes = (
            self.DEFAULT_SKIP_PREFIXES if skip_prefixes is None
            else tuple(skip_prefixes)
        )
        self._existing: set[str] = set()

    # --- TripleSource API --------------------------------------------------

    def ingest(
        self,
        store: TriplesStore,
        limit: int = 1000,
        since: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> int:
        # Pre-load known tool_use_ids so re-runs are idempotent.
        cur = store.conn.execute(
            "SELECT context_data FROM triples "
            "WHERE source = ? AND context_data IS NOT NULL",
            (self.name,),
        )
        for (ctx_json,) in cur.fetchall():
            try:
                ctx = json.loads(ctx_json)
            except (TypeError, ValueError):
                continue
            tid = ctx.get("tool_use_id") if isinstance(ctx, dict) else None
            if tid:
                self._existing.add(tid)

        return super().ingest(
            store, limit=limit, since=since, project_id=project_id,
        )

    def fetch(
        self, limit: int = 1000, since: Optional[str] = None
    ) -> Iterator[TripleRecord]:
        slug = _slugify_cwd(self.project_path)
        proj_dir = self.claude_root / slug
        if not proj_dir.exists():
            return

        uses: dict[str, tuple[dict, dict, str]] = {}
        results: dict[str, tuple[dict, dict, str]] = {}
        for jf in sorted(proj_dir.glob("*.jsonl")):
            for entry in _iter_jsonl(jf):
                msg = entry.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content") or []
                if not isinstance(content, list):
                    continue
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    btype = blk.get("type")
                    if btype == "tool_use":
                        bid = blk.get("id")
                        if bid:
                            uses[bid] = (entry, blk, jf.name)
                    elif btype == "tool_result":
                        tuid = blk.get("tool_use_id")
                        if tuid:
                            results[tuid] = (entry, blk, jf.name)

        # Iterate paired calls in chronological order so created_at stays
        # monotonic when we forward it.
        ordered = sorted(
            (k for k in uses if k in results),
            key=lambda k: uses[k][0].get("timestamp") or "",
        )
        if since:
            ordered = [k for k in ordered if (uses[k][0].get("timestamp") or "") >= since]

        yielded = 0
        for use_id in ordered:
            if yielded >= limit:
                break
            if use_id in self._existing:
                continue

            u_obj, u_blk, u_file = uses[use_id]
            r_obj, r_blk, _ = results[use_id]

            tool_name = u_blk.get("name") or "?"
            if any(tool_name.startswith(p) for p in self.skip_prefixes):
                continue

            if self.skip_errors and r_blk.get("is_error"):
                continue

            output_text = _extract_result_text(r_blk)

            yield TripleRecord(
                tool_name=tool_name,
                input_data=u_blk.get("input") or {},
                output_data=output_text,
                context_data={
                    "tool_use_id": use_id,
                    "msg_uuid": u_obj.get("uuid"),
                    "cwd": u_obj.get("cwd"),
                    "use_ts": u_obj.get("timestamp"),
                    "result_ts": r_obj.get("timestamp"),
                    "is_error": bool(r_blk.get("is_error")),
                    "jsonl_file": u_file,
                },
                session_id=u_obj.get("sessionId"),
                created_at=u_obj.get("timestamp"),
            )
            yielded += 1


# --- helpers ---------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed entries from a JSONL file. Skips blank/malformed lines."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _extract_result_text(blk: dict) -> str:
    """tool_result.content can be a str OR a list of content blocks. Flatten
    to text. Non-text blocks (images etc.) are stringified with type tags."""
    content = blk.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text") or "")
                elif "text" in c:
                    parts.append(str(c["text"]))
                else:
                    parts.append(json.dumps(c))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)
