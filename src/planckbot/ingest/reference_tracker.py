"""Reference-tracking signal: infer `filtered_output` from what the host LLM
actually cited in its next message.

A tool returns `output`. The host LLM (Claude, GPT, …) reads it and replies
with some text. If that reply quotes line X of `output` verbatim, line X was
useful; otherwise it was probably noise that a PlanckBot adapter should have
filtered out. This module implements the matching primitive.

Matching rule (v1, intentionally dumb): a line is "referenced" iff its
stripped form appears as a substring of the reference text and is at least
`min_line_len` chars long. Short lines (e.g. `{`, `}`, `[`) are skipped as
structural noise that trivially appears everywhere.
"""

from __future__ import annotations

import re


# Words that appear in every tool output because they're structural markers
# ([DIR], [FILE], …). They'd trivially match any reference if we didn't ignore
# them, producing false positives.
_STRUCTURAL_NOISE = {"dir", "file", "http", "https"}


def _word_tokens(text: str, min_len: int) -> set[str]:
    """Split on non-word characters, keep lowercased tokens ≥ min_len."""
    return {
        tok.lower()
        for tok in re.split(r"[^A-Za-z0-9_]+", text)
        if len(tok) >= min_len
    }


def extract_referenced_lines(
    output: str,
    reference: str,
    *,
    min_line_len: int = 3,
    match_mode: str = "token",
) -> str | None:
    """Return lines from `output` that appear in `reference`.

    Two matching strategies:

    - `substring` (the original, kept for back-compat): a line is kept iff
      its stripped form is a substring of the reference. Dumb but sometimes
      precise for JSON/structured logs.
    - `token` (new default): split both line and reference on non-word
      characters, keep the line iff it shares at least one non-structural
      word token (≥ min_line_len chars) with the reference. Matches the
      reality that Claude usually paraphrases tool output — `[DIR] app`
      becomes `**app**` in its reply. Token matching picks that up;
      substring matching does not.

    Returns None when nothing matched.
    """
    if not output or not reference:
        return None

    if match_mode == "substring":
        kept: list[str] = []
        for line in output.splitlines():
            stripped = line.strip()
            if len(stripped) < min_line_len:
                continue
            if stripped in reference:
                kept.append(line)
        return "\n".join(kept) if kept else None

    if match_mode == "token":
        ref_tokens = _word_tokens(reference, min_line_len)
        if not ref_tokens:
            return None
        kept = []
        for line in output.splitlines():
            tokens = _word_tokens(line, min_line_len) - _STRUCTURAL_NOISE
            if tokens & ref_tokens:
                kept.append(line)
        return "\n".join(kept) if kept else None

    raise ValueError(f"unknown match_mode: {match_mode!r}")


def label_triple_from_reference(
    store,
    triple_id: str,
    reference: str,
    *,
    min_line_len: int = 3,
    force: bool = False,
    match_mode: str = "token",
) -> str | None:
    """Populate `filtered_output` on an existing triple.

    No-op (returns existing value) when the triple already has a
    `filtered_output` unless `force=True`. Returns the kept string or None.
    """
    triple = store.get(triple_id)
    if triple is None:
        raise ValueError(f"triple not found: {triple_id}")

    if triple.filtered_output and not force:
        return triple.filtered_output

    kept = extract_referenced_lines(
        triple.output_data, reference,
        min_line_len=min_line_len, match_mode=match_mode,
    )
    if kept is None:
        return None

    store.update_filtered(triple_id, kept)
    return kept
