"""Tier-1 bench techniques. Each one is a `Technique` subclass that computes
a ceiling estimate for one optimization idea, replayed over real triples.

All techniques here use ONLY data already in `data/planckbot.db` plus
optional JSONL references — no model in the loop, no API calls.

Conventions:
- `tokens_saved` is the per-triple ceiling under "if this worked perfectly".
- `tokens_at_risk` is the proxy false-positive count: tokens dropped that
  may have been needed downstream. Higher = less trustworthy ceiling.
- Techniques NEVER mutate input. Stateful techniques use `reset_session`
  to clear per-session state.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from planckbot.bench.harness import (
    Technique, TechniqueResult, SessionView, TripleEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_input(triple) -> dict:
    """Triple.input_data is stored as JSON string. Parse defensively."""
    raw = triple.input_data
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _approx_tokens(s: str) -> int:
    return max(1, len(s) // 4) if s else 0


# ---------------------------------------------------------------------------
# A. Cache-deny on repeat Reads
# ---------------------------------------------------------------------------


class CacheDenyRead(Technique):
    """For each Read of a path already seen this session, the second+ Read
    is cacheable. Risk = if a Write/Edit of the same path landed between
    them (file changed → cache stale)."""
    name = "A.cache_deny_read"

    def reset_session(self, session: SessionView) -> None:
        self._last_seen: dict[str, int] = {}    # path -> position
        self._dirty: set[str] = set()           # paths edited this session

    def apply(self, event, session):
        t = event.triple
        if t.tool_name != "Read":
            self._maybe_mark_dirty(event)
            return TechniqueResult()
        inp = _parse_input(t)
        path = inp.get("file_path") or inp.get("path") or inp.get("filename")
        if not isinstance(path, str):
            return TechniqueResult()
        out_tokens = t.output_tokens or 0
        if path in self._last_seen:
            at_risk = out_tokens if path in self._dirty else 0
            self._last_seen[path] = event.position
            self._dirty.discard(path)
            return TechniqueResult(
                affected=True,
                tokens_saved=out_tokens,
                tokens_at_risk=at_risk,
                notes=f"repeat read of {path}",
            )
        self._last_seen[path] = event.position
        return TechniqueResult()

    def _maybe_mark_dirty(self, event):
        t = event.triple
        if t.tool_name not in ("Edit", "Write", "MultiEdit"):
            return
        inp = _parse_input(t)
        path = inp.get("file_path") or inp.get("path") or inp.get("filename")
        if isinstance(path, str) and path in self._last_seen:
            self._dirty.add(path)


# ---------------------------------------------------------------------------
# B. Bash exact-dedup cache
# ---------------------------------------------------------------------------


class BashExactDedup(Technique):
    """A Bash call whose (command, cwd) we've already seen verbatim THIS
    session is cacheable. No safe-list needed — exact-match-only is a
    floor estimate of the idempotent-cache idea.
    """
    name = "B.bash_exact_dedup"

    def reset_session(self, session: SessionView) -> None:
        self._seen: dict[tuple[str, str], int] = {}

    def apply(self, event, session):
        t = event.triple
        if t.tool_name != "Bash":
            return TechniqueResult()
        inp = _parse_input(t)
        cmd = inp.get("command")
        if not isinstance(cmd, str):
            return TechniqueResult()
        ctx = event.context()
        cwd = ctx.get("cwd", "")
        key = (cmd.strip(), cwd)
        if key in self._seen:
            return TechniqueResult(
                affected=True,
                tokens_saved=t.output_tokens or 0,
                tokens_at_risk=0,  # exact dedup is zero-risk by definition
                notes=f"repeat bash: {cmd[:40]}",
            )
        self._seen[key] = event.position
        return TechniqueResult()


# ---------------------------------------------------------------------------
# D. Diff re-reads
# ---------------------------------------------------------------------------


class DiffReReads(Technique):
    """Read → Edit/Write → Read of the same path. The second Read could
    have been a diff vs the first. Ceiling = output_tokens - estimated
    diff size (we use 30% of original as a conservative diff)."""
    name = "D.diff_reads"

    DIFF_FRAC = 0.30  # estimated size of edit-diff vs full file

    def reset_session(self, session: SessionView) -> None:
        # path -> {"last_pos": int, "last_tokens": int, "edited_since": bool}
        self._state: dict[str, dict] = {}

    def apply(self, event, session):
        t = event.triple
        inp = _parse_input(t)
        path = inp.get("file_path") or inp.get("path")
        if not isinstance(path, str):
            return TechniqueResult()

        if t.tool_name in ("Edit", "Write", "MultiEdit"):
            if path in self._state:
                self._state[path]["edited_since"] = True
            return TechniqueResult()

        if t.tool_name != "Read":
            return TechniqueResult()

        out_tokens = t.output_tokens or 0
        prev = self._state.get(path)
        if prev and prev["edited_since"]:
            saved = int(out_tokens * (1 - self.DIFF_FRAC))
            self._state[path] = {
                "last_pos": event.position,
                "last_tokens": out_tokens,
                "edited_since": False,
            }
            return TechniqueResult(
                affected=True,
                tokens_saved=saved,
                tokens_at_risk=0,  # diff still contains the edited region
                notes=f"diff-rereread: {path}",
            )
        self._state[path] = {
            "last_pos": event.position,
            "last_tokens": out_tokens,
            "edited_since": False,
        }
        return TechniqueResult()


# ---------------------------------------------------------------------------
# G. Retry detection
# ---------------------------------------------------------------------------


class RetryDetection(Technique):
    """Tool T called twice in a row in the same session where the second
    call has a similar-but-different first-arg. Likely a retry after a
    failure or correction. Ceiling = tokens we'd have skipped if we'd
    predicted the failure before invoking."""
    name = "G.retry_detection"

    def reset_session(self, session: SessionView) -> None:
        self._last: dict[str, tuple[int, str]] = {}  # tool_name -> (pos, first_arg_str)

    def apply(self, event, session):
        t = event.triple
        inp = _parse_input(t)
        # First-arg fingerprint: union of likely-identifier keys.
        fp = (
            inp.get("file_path") or inp.get("path") or inp.get("command")
            or inp.get("pattern") or inp.get("name") or ""
        )
        if not isinstance(fp, str):
            return TechniqueResult()
        prev = self._last.get(t.tool_name)
        self._last[t.tool_name] = (event.position, fp)
        if not prev or not fp:
            return TechniqueResult()
        prev_pos, prev_fp = prev
        if event.position - prev_pos != 1:
            return TechniqueResult()  # not back-to-back
        if not prev_fp or fp == prev_fp:
            return TechniqueResult()  # not a retry (or exact dup, B's domain)
        if _similar(prev_fp, fp):
            return TechniqueResult(
                affected=True,
                tokens_saved=(t.output_tokens or 0),
                tokens_at_risk=0,
                notes=f"retry of {t.tool_name}",
            )
        return TechniqueResult()


def _similar(a: str, b: str) -> bool:
    """Cheap edit-distance proxy: >=80% character overlap of the shorter."""
    if not a or not b:
        return False
    sa, sb = (a, b) if len(a) <= len(b) else (b, a)
    if len(sa) < 4:
        return False
    if sa in sb:
        return True
    overlap = sum(1 for ch in sa if ch in sb)
    return overlap / len(sa) >= 0.8


# ---------------------------------------------------------------------------
# M. Argument auto-correct
# ---------------------------------------------------------------------------


class ArgAutoCorrect(Technique):
    """Subset of G: same tool back-to-back, args nearly identical (single
    typo / off-by-one). Ceiling = first call's output (we'd have produced
    the corrected call directly). Distinct technique because the fix is
    'rewrite the call', not just 'predict failure'."""
    name = "M.arg_autocorrect"

    def reset_session(self, session: SessionView) -> None:
        self._last_input: dict[str, tuple[int, str]] = {}

    def apply(self, event, session):
        t = event.triple
        inp = _parse_input(t)
        signature = json.dumps(inp, sort_keys=True)[:500]
        prev = self._last_input.get(t.tool_name)
        self._last_input[t.tool_name] = (event.position, signature)
        if not prev:
            return TechniqueResult()
        prev_pos, prev_sig = prev
        if event.position - prev_pos != 1:
            return TechniqueResult()
        if prev_sig == signature:
            return TechniqueResult()  # exact dup, not this technique
        if _levenshtein_capped(prev_sig, signature, cap=5) <= 3:
            return TechniqueResult(
                affected=True,
                tokens_saved=(t.output_tokens or 0),
                tokens_at_risk=0,
                notes=f"autocorrect {t.tool_name}",
            )
        return TechniqueResult()


def _levenshtein_capped(a: str, b: str, cap: int = 5) -> int:
    """Bounded Levenshtein — returns cap+1 the instant we exceed cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for j, cb in enumerate(b, 1):
        cur = [j] + [0] * len(a)
        for i, ca in enumerate(a, 1):
            cur[i] = min(
                prev[i] + 1,
                cur[i - 1] + 1,
                prev[i - 1] + (0 if ca == cb else 1),
            )
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# J. Uncited-in-session compaction
# ---------------------------------------------------------------------------


class UncitedCompaction(Technique):
    """A triple whose output content never appears as substring of any
    later triple's input in the same session can be compacted aggressively.
    Ceiling = full output_tokens. Risk = the content was used in an
    assistant turn we don't see (assistant text isn't in triples).
    """
    name = "J.uncited_compaction"

    MIN_LINE_LEN = 8   # ignore noise lines

    def reset_session(self, session: SessionView) -> None:
        # Pre-compute all subsequent inputs concatenated per position.
        # We need lookahead, so we do it lazily on first apply().
        self._later_text: list[str] = []
        self._cached_session_id: str | None = None

    def _ensure_cache(self, session: SessionView):
        if self._cached_session_id == session.session_id:
            return
        all_inputs = [ev.triple.input_data or "" for ev in session.events]
        # Cumulative from the end: later_text[i] = concat(all_inputs[i+1:])
        suffix = [""] * (len(all_inputs) + 1)
        for i in range(len(all_inputs) - 1, -1, -1):
            suffix[i] = suffix[i + 1] + " " + all_inputs[i + 1] if i + 1 < len(all_inputs) else ""
        self._later_text = suffix
        self._cached_session_id = session.session_id

    def apply(self, event, session):
        t = event.triple
        out = t.output_data or ""
        if not out:
            return TechniqueResult()
        self._ensure_cache(session)
        later = self._later_text[event.position]
        # Tokenize output lines; count how many appear (substring) in later.
        cited_chars = 0
        total_chars = 0
        for line in out.splitlines():
            stripped = line.strip()
            if len(stripped) < self.MIN_LINE_LEN:
                continue
            total_chars += len(stripped)
            if stripped in later:
                cited_chars += len(stripped)
        if total_chars == 0:
            return TechniqueResult()
        uncited_frac = 1 - (cited_chars / total_chars)
        if uncited_frac < 0.8:
            return TechniqueResult()
        saved = int((t.output_tokens or 0) * uncited_frac)
        return TechniqueResult(
            affected=True,
            tokens_saved=saved,
            tokens_at_risk=saved,  # whole signal could be assistant-text-cited
            notes=f"uncited_frac={uncited_frac:.2f}",
        )


# ---------------------------------------------------------------------------
# K. Layer D ngram synthesis ceiling
# ---------------------------------------------------------------------------


class NgramSynthesis(Technique):
    """For each tool sequence (e.g. Read→Edit→Bash) that repeats N≥3
    times in a session within a 60s window, a synthesized compound tool
    would collapse the round-trips. Ceiling per match = (N-1) × per-call
    overhead. We charge the savings to the LAST event of each matched
    sequence so the per-triple replay accounting still works."""
    name = "K.ngram_synthesis"

    WINDOW_SECONDS = 60.0
    NGRAM = 3
    PER_CALL_OVERHEAD = 80  # tokens: tool_use boilerplate + result wrapper

    def reset_session(self, session: SessionView) -> None:
        self._marks: dict[int, int] = {}  # position -> tokens_saved
        self._compute(session)

    def _compute(self, session: SessionView):
        n = len(session.events)
        if n < self.NGRAM:
            return
        # Build chronological window-aware n-gram occurrences.
        # Match if next n events are within WINDOW_SECONDS of each other.
        ts = []
        for ev in session.events:
            t = ev.triple.created_at or ""
            ts.append(t)
        seq_counts: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for i in range(n - self.NGRAM + 1):
            window = session.events[i:i + self.NGRAM]
            names = tuple(e.triple.tool_name for e in window)
            # Time span check
            t0, tN = window[0].triple.created_at, window[-1].triple.created_at
            if t0 and tN:
                try:
                    from datetime import datetime
                    a = datetime.fromisoformat(t0.replace("Z", "+00:00"))
                    b = datetime.fromisoformat(tN.replace("Z", "+00:00"))
                    if (b - a).total_seconds() > self.WINDOW_SECONDS:
                        continue
                except Exception:
                    pass
            seq_counts[names].append(i)
        for seq, positions in seq_counts.items():
            if len(positions) < 2:
                continue
            # Mark the LAST event of each occurrence with its share of the savings.
            for start in positions:
                last_pos = start + self.NGRAM - 1
                self._marks[last_pos] = (
                    self._marks.get(last_pos, 0)
                    + self.PER_CALL_OVERHEAD * (self.NGRAM - 1)
                )

    def apply(self, event, session):
        saved = self._marks.get(event.position, 0)
        if not saved:
            return TechniqueResult()
        return TechniqueResult(
            affected=True,
            tokens_saved=saved,
            tokens_at_risk=0,
            notes="ngram synth ceiling",
        )


# ---------------------------------------------------------------------------
# L. Split tools (in-session token overlap variant)
# ---------------------------------------------------------------------------


class SplitToolsLocal(Technique):
    """For each Read, what fraction of the output's lines NEVER appear as
    substring in any later triple's input in the session? That fraction
    could have been filtered out by a hypothetical `read_relevant` tool.
    Triples-only variant; conservative because true citations happen in
    assistant text (use the JSONL variant for the honest number).
    """
    name = "L.split_tools_local"

    MIN_LINE_LEN = 6

    def reset_session(self, session: SessionView) -> None:
        self._later_text: list[str] = []
        self._cached_session_id: str | None = None

    def _ensure_cache(self, session: SessionView):
        if self._cached_session_id == session.session_id:
            return
        all_inputs = [ev.triple.input_data or "" for ev in session.events]
        suffix = [""] * (len(all_inputs) + 1)
        for i in range(len(all_inputs) - 1, -1, -1):
            suffix[i] = (
                suffix[i + 1] + " " + all_inputs[i + 1]
                if i + 1 < len(all_inputs) else ""
            )
        self._later_text = suffix
        self._cached_session_id = session.session_id

    def apply(self, event, session):
        t = event.triple
        if t.tool_name != "Read":
            return TechniqueResult()
        out = t.output_data or ""
        if not out:
            return TechniqueResult()
        self._ensure_cache(session)
        later = self._later_text[event.position]
        kept_chars = 0
        total_chars = 0
        for line in out.splitlines():
            s = line.strip()
            if len(s) < self.MIN_LINE_LEN:
                continue
            total_chars += len(s)
            if s in later:
                kept_chars += len(s)
        if total_chars == 0:
            return TechniqueResult()
        drop_frac = 1 - (kept_chars / total_chars)
        if drop_frac < 0.30:
            return TechniqueResult()
        saved = int((t.output_tokens or 0) * drop_frac)
        # at_risk: in-session signal misses assistant-text citations
        return TechniqueResult(
            affected=True,
            tokens_saved=saved,
            tokens_at_risk=int(saved * 0.5),
            notes=f"drop_frac={drop_frac:.2f}",
        )


# ---------------------------------------------------------------------------
# L (JSONL-honest). Same as L but uses next-assistant-text from JSONL.
# ---------------------------------------------------------------------------


class SplitToolsJsonl(Technique):
    """JSONL-honest variant of L: uses the actual assistant text after each
    tool_result as the citation reference (à la Orquesta's methodology).
    Requires a reference index built from JSONL transcripts.
    """
    name = "L.split_tools_jsonl"

    MIN_LINE_LEN = 6

    def __init__(self, references: dict[str, str]):
        self.references = references

    def apply(self, event, session):
        t = event.triple
        if t.tool_name != "Read":
            return TechniqueResult()
        out = t.output_data or ""
        if not out:
            return TechniqueResult()
        tool_use_id = event.context().get("tool_use_id")
        ref = self.references.get(tool_use_id or "")
        if not ref:
            return TechniqueResult()
        # Token-overlap citation rule (same as reference_tracker token mode).
        ref_tokens = set(re.split(r"[^A-Za-z0-9_]+", ref.lower()))
        ref_tokens -= {"", "dir", "file", "http", "https"}
        kept_chars = 0
        total_chars = 0
        for line in out.splitlines():
            s = line.strip()
            if len(s) < self.MIN_LINE_LEN:
                continue
            total_chars += len(s)
            line_tokens = set(re.split(r"[^A-Za-z0-9_]+", s.lower()))
            if line_tokens & ref_tokens:
                kept_chars += len(s)
        if total_chars == 0:
            return TechniqueResult()
        drop_frac = 1 - (kept_chars / total_chars)
        if drop_frac < 0.30:
            return TechniqueResult()
        saved = int((t.output_tokens or 0) * drop_frac)
        return TechniqueResult(
            affected=True,
            tokens_saved=saved,
            tokens_at_risk=int(saved * 0.4),
            notes=f"drop={drop_frac:.2f}",
        )


# ---------------------------------------------------------------------------
# N. Bash early-termination
# ---------------------------------------------------------------------------


class BashEarlyTerm(Technique):
    """For each Bash with output > 200 tokens, find the LAST line that
    appears in any later triple's input in the session. Lines after that
    are tail noise that could have been truncated by a streaming early
    terminator. Ceiling = tokens after the last-citation position."""
    name = "N.bash_early_term"

    MIN_OUTPUT_TOKENS = 200
    MIN_LINE_LEN = 6

    def reset_session(self, session: SessionView) -> None:
        self._later_text: list[str] = []
        self._cached_session_id: str | None = None

    def _ensure_cache(self, session: SessionView):
        if self._cached_session_id == session.session_id:
            return
        all_inputs = [ev.triple.input_data or "" for ev in session.events]
        suffix = [""] * (len(all_inputs) + 1)
        for i in range(len(all_inputs) - 1, -1, -1):
            suffix[i] = (
                suffix[i + 1] + " " + all_inputs[i + 1]
                if i + 1 < len(all_inputs) else ""
            )
        self._later_text = suffix
        self._cached_session_id = session.session_id

    def apply(self, event, session):
        t = event.triple
        if t.tool_name != "Bash":
            return TechniqueResult()
        if (t.output_tokens or 0) < self.MIN_OUTPUT_TOKENS:
            return TechniqueResult()
        self._ensure_cache(session)
        later = self._later_text[event.position]
        lines = (t.output_data or "").splitlines()
        last_hit = -1
        for i, line in enumerate(lines):
            s = line.strip()
            if len(s) < self.MIN_LINE_LEN:
                continue
            if s in later:
                last_hit = i
        if last_hit < 0:
            # nothing cited at all → don't claim savings here
            # (UncitedCompaction handles this case)
            return TechniqueResult()
        # Lines after last_hit are tail noise
        tail = lines[last_hit + 1:]
        tail_chars = sum(len(ln) for ln in tail)
        if tail_chars < 100:
            return TechniqueResult()
        total_chars = sum(len(ln) for ln in lines) or 1
        saved = int((t.output_tokens or 0) * tail_chars / total_chars)
        return TechniqueResult(
            affected=True,
            tokens_saved=saved,
            tokens_at_risk=int(saved * 0.2),  # tail might contain summary
            notes=f"tail_frac={tail_chars/total_chars:.2f}",
        )


# ---------------------------------------------------------------------------
# V. Tool budget per-session
# ---------------------------------------------------------------------------


class ToolBudget(Technique):
    """Sessions in the top 10% by tool-call count are likely doing some
    kind of redundant work (long loops, repeated explorations). Ceiling
    is the tokens in calls beyond the 90th-percentile session length —
    we'd have cut the session short if a budget were enforced.

    Charged to the LAST event of each over-budget session.
    """
    name = "V.tool_budget"

    BUDGET_PERCENTILE = 0.90

    def __init__(self):
        self._budget_size: int | None = None
        self._marks: dict[str, int] = {}

    def calibrate(self, sessions: list[SessionView]) -> None:
        """Compute the 90th-percentile session size across the dataset.
        Must be called before replay()."""
        sizes = sorted(len(s) for s in sessions)
        if not sizes:
            self._budget_size = 0
            return
        idx = int(len(sizes) * self.BUDGET_PERCENTILE)
        self._budget_size = sizes[min(idx, len(sizes) - 1)]

    def reset_session(self, session: SessionView) -> None:
        if self._budget_size is None:
            self._budget_size = 50  # safe default
        if len(session) <= self._budget_size:
            self._marks[session.session_id] = 0
            return
        excess = session.events[self._budget_size:]
        total_tokens = sum(
            (ev.triple.output_tokens or 0) for ev in excess
        )
        self._marks[session.session_id] = total_tokens

    def apply(self, event, session):
        saved = self._marks.get(session.session_id, 0)
        if not saved:
            return TechniqueResult()
        # charge only the LAST event of session
        if event.position != len(session) - 1:
            return TechniqueResult()
        return TechniqueResult(
            affected=True,
            tokens_saved=saved,
            tokens_at_risk=saved,  # we don't know which calls were useful
            notes=f"over-budget by {len(session) - self._budget_size}",
        )


# ---------------------------------------------------------------------------
# T. Dead-end prompts (JSONL-based)
# ---------------------------------------------------------------------------


class DeadEndPrompts(Technique):
    """If the next-assistant-text for a triple looks like a clarification
    request ('could you clarify', 'I need more info', etc.) the entire
    session up to that point may have been a dead-end that a faster
    tiny-model gate would have caught before invoking Claude. Ceiling =
    output_tokens of the triple (proxy for 'tokens spent on dead-end').
    """
    name = "T.dead_end_prompts"

    CLARIFY_RE = re.compile(
        r"(?:could you clarify|need more (?:info|context|details)|"
        r"can you (?:explain|elaborate|specify)|"
        r"i'm not sure what you (?:mean|want)|"
        r"what (?:exactly )?do you mean|"
        r"please clarify)",
        re.IGNORECASE,
    )

    def __init__(self, references: dict[str, str]):
        self.references = references

    def apply(self, event, session):
        t = event.triple
        tid = event.context().get("tool_use_id")
        ref = self.references.get(tid or "")
        if not ref:
            return TechniqueResult()
        if not self.CLARIFY_RE.search(ref):
            return TechniqueResult()
        return TechniqueResult(
            affected=True,
            tokens_saved=(t.output_tokens or 0),
            tokens_at_risk=0,  # if next turn was clarification, this WAS dead-end
            notes="clarify-ack",
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def all_triples_only_techniques() -> list[Technique]:
    """Techniques that don't need JSONL references — runnable from DB only."""
    return [
        CacheDenyRead(), BashExactDedup(), DiffReReads(),
        RetryDetection(), ArgAutoCorrect(),
        UncitedCompaction(), SplitToolsLocal(),
        NgramSynthesis(), BashEarlyTerm(),
        ToolBudget(),
    ]


def jsonl_techniques(references: dict[str, str]) -> list[Technique]:
    """Techniques that need a tool_use_id -> next_assistant_text map."""
    return [
        SplitToolsJsonl(references), DeadEndPrompts(references),
    ]
