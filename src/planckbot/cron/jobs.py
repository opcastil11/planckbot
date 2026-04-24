"""Pluggable job types for the cron engine.

Adding a new job type = one function. The function takes a `JobContext`
(params + db connection + stores) and returns a short human-readable status
string that gets stored in `cron_jobs.last_output`. Raise an exception to
mark the job run as failed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from planckbot.tools.triples import TriplesStore


JobFn = Callable[["JobContext"], str]


@dataclass
class JobContext:
    """Everything a job function needs at run time."""
    conn: sqlite3.Connection
    params: dict = field(default_factory=dict)


class JobRegistry:
    def __init__(self):
        self._jobs: dict[str, JobFn] = {}

    def register(self, job_type: str, fn: JobFn) -> None:
        self._jobs[job_type] = fn

    def get(self, job_type: str) -> JobFn | None:
        return self._jobs.get(job_type)

    def types(self) -> list[str]:
        return sorted(self._jobs.keys())


# --- built-in job types ----------------------------------------------------


def _noop_job(ctx: JobContext) -> str:
    """Sanity-check job. Returns the `message` param verbatim."""
    return f"noop: {ctx.params.get('message', 'hello')}"


def _autolabel_job(ctx: JobContext) -> str:
    """Back-fill `filtered_output` on recent unlabeled triples for a tool.

    params:
        tool (str)           — tool name (required)
        reference_path (str) — file with the host-LLM reference text (required)
        recent (int)         — how many newest unlabeled triples to process (default 10)
        min_line_len (int)   — noise filter for short lines (default 3)
    """
    from pathlib import Path

    from planckbot.ingest.reference_tracker import label_triple_from_reference

    tool = ctx.params.get("tool")
    ref_path = ctx.params.get("reference_path")
    recent = int(ctx.params.get("recent", 10))
    min_line_len = int(ctx.params.get("min_line_len", 3))

    if not tool or not ref_path:
        raise ValueError("autolabel requires params.tool and params.reference_path")

    reference = Path(ref_path).read_text()
    store = TriplesStore(ctx.conn)
    candidates = store.list_unlabeled(tool_name=tool, limit=recent)

    if not candidates:
        return f"no unlabeled triples for tool={tool}"

    labeled = 0
    skipped = 0
    for t in candidates:
        kept = label_triple_from_reference(
            store, t.id, reference, min_line_len=min_line_len
        )
        if kept is None:
            skipped += 1
        else:
            labeled += 1
    return f"tool={tool} labeled={labeled} skipped={skipped} from {len(candidates)}"


def _retrain_job(ctx: JobContext) -> str:
    """Retrain the active adapter for a tool if enough new labeled triples
    have accumulated since the last training run.

    params:
        tool (str)            — tool name (required)
        fixture_path (str)    — ingested on each run (required — seed data lives here)
        min_new_labeled (int) — floor on labeled triples vs last ckpt (default 10)
        epochs (int)          — passed through to train_tool (default 3)

    This is a thin wrapper over `scripts/train_tool.py` via its functions; we
    import them lazily to avoid loading torch into the daemon before a
    training is actually requested.
    """
    tool = ctx.params.get("tool")
    if not tool:
        raise ValueError("retrain requires params.tool")

    min_new = int(ctx.params.get("min_new_labeled", 10))

    store = TriplesStore(ctx.conn)
    # Count labeled triples for this tool.
    row = ctx.conn.execute(
        "SELECT COUNT(*) FROM triples WHERE tool_name = ? "
        "AND filtered_output IS NOT NULL",
        (tool,),
    ).fetchone()
    labeled = int(row[0] or 0)

    # Count labeled triples produced AFTER the most recent checkpoint for this tool.
    ckpt_row = ctx.conn.execute(
        "SELECT MAX(created_at) FROM model_checkpoints WHERE tool_name = ?",
        (tool,),
    ).fetchone()
    last_ckpt_at = ckpt_row[0]
    if last_ckpt_at is not None:
        row2 = ctx.conn.execute(
            "SELECT COUNT(*) FROM triples WHERE tool_name = ? "
            "AND filtered_output IS NOT NULL AND created_at > ?",
            (tool, last_ckpt_at),
        ).fetchone()
        new_since = int(row2[0] or 0)
    else:
        new_since = labeled

    if new_since < min_new:
        return (
            f"tool={tool} skip: labeled_total={labeled} "
            f"new_since_last_ckpt={new_since} < {min_new}"
        )

    # We DON'T kick off a real training from inside the daemon — that blocks
    # the scheduler for ~10+ min on CPU. Instead, record the intent so the
    # user (or an external worker) can pick it up.
    return (
        f"tool={tool} ready_to_retrain: labeled_total={labeled} "
        f"new_since_last_ckpt={new_since} — "
        f"run `.venv/bin/python scripts/train_tool.py --tool {tool} "
        f"--fixture {ctx.params.get('fixture_path', '<path>')} "
        f"--epochs {int(ctx.params.get('epochs', 3))} --activate`"
    )


def _autolabel_precise_job(ctx: JobContext) -> str:
    """Per-triple auto-label using Claude Code JSONL logs.

    For each unlabeled triple of `tool`, finds the specific tool_use block
    in the project's JSONL and uses ONLY the assistant message that followed
    that call as the reference. This eliminates the false-positive cross-
    contamination the batched `autolabel` job suffers from.

    params:
        tool (str)            — tool name (required)
        project_slug (str)    — Claude Code project dir slug. Optional:
                                defaults to the slugified cwd.
        recent (int)          — how many newest unlabeled triples to try
                                (default 20)
        min_line_len (int)    — noise filter (default 3)
        max_drift_seconds (int) — how far in time the tool_use timestamp
                                may drift from the triple.created_at
                                (default 180)
        claude_root (str)     — override for tests (default ~/.claude/projects)
    """
    from datetime import datetime
    from pathlib import Path

    from planckbot.cron.scanner import (
        _slugify_cwd, _parse_ts, find_response_after_tool_call,
    )
    from planckbot.ingest.reference_tracker import label_triple_from_reference
    from planckbot.tools.triples import TriplesStore

    tool = ctx.params.get("tool")
    if not tool:
        raise ValueError("autolabel_precise requires params.tool")

    slug = ctx.params.get("project_slug") or _slugify_cwd(Path.cwd())
    recent = int(ctx.params.get("recent", 20))
    min_line_len = int(ctx.params.get("min_line_len", 3))
    max_drift = float(ctx.params.get("max_drift_seconds", 180))
    claude_root = ctx.params.get("claude_root")
    project_dir = (
        Path(claude_root) if claude_root
        else Path.home() / ".claude" / "projects"
    ) / slug

    if not project_dir.exists():
        return f"project dir not found: {project_dir}"

    jsonl_files = sorted(project_dir.glob("*.jsonl"))
    if not jsonl_files:
        return f"no jsonl files in {project_dir}"

    store = TriplesStore(ctx.conn)
    candidates = store.list_unlabeled(tool_name=tool, limit=recent)
    if not candidates:
        return f"no unlabeled triples for tool={tool}"

    labeled = skipped = 0
    for t in candidates:
        triple_ts = _parse_ts(t.created_at)
        if triple_ts is None:
            skipped += 1
            continue
        # Try each jsonl file until we find a match
        reference: str | None = None
        for jf in jsonl_files:
            reference = find_response_after_tool_call(
                jf,
                tool_name=tool,
                input_data=t.input_data,
                near_ts=triple_ts,
                max_drift_seconds=max_drift,
            )
            if reference is not None:
                break

        if reference is None:
            skipped += 1
            continue

        kept = label_triple_from_reference(
            store, t.id, reference, min_line_len=min_line_len,
        )
        if kept is None:
            skipped += 1
        else:
            labeled += 1

    return (
        f"tool={tool} project={slug} candidates={len(candidates)} "
        f"labeled={labeled} skipped(no-match)={skipped}"
    )


def _detect_tool_gaps_job(ctx: JobContext) -> str:
    """Run the Layer-D pattern detector over recent triples and upsert
    gap_reports rows. Params:

        window_seconds (float)  — max span for an N-gram (default 30)
        min_occurrences (int)   — floor to consider a sequence a gap (default 2)
        limit_triples (int)     — look back over at most this many triples
                                  (default 500)
    """
    from planckbot.synth.detector import build_gap_reports, find_tool_sequences
    from planckbot.tools.triples import TriplesStore

    params = ctx.params
    window = float(params.get("window_seconds", 30.0))
    min_occ = int(params.get("min_occurrences", 2))
    limit = int(params.get("limit_triples", 500))

    triples = TriplesStore(ctx.conn).list_all(limit=limit)
    matches = find_tool_sequences(
        triples,
        window_seconds=window,
        min_occurrences=min_occ,
    )
    if not matches:
        return f"no repeated sequences (window={window}s min={min_occ})"
    created, updated = build_gap_reports(ctx.conn, matches)
    top = matches[0]
    return (
        f"matches={len(matches)} created={created} updated={updated} "
        f"top={' → '.join(top.sequence)} ({top.occurrences}x)"
    )


def default_registry() -> JobRegistry:
    from planckbot.cron.scanner import scan_job as _scan_job
    reg = JobRegistry()
    reg.register("noop", _noop_job)
    reg.register("autolabel", _autolabel_job)
    reg.register("retrain", _retrain_job)
    reg.register("conversation_scanner", _scan_job)
    reg.register("detect_tool_gaps", _detect_tool_gaps_job)
    reg.register("autolabel_precise", _autolabel_precise_job)
    return reg
