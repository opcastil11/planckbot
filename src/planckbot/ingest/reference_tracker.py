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


def extract_referenced_lines(
    output: str,
    reference: str,
    *,
    min_line_len: int = 3,
) -> str | None:
    """Return lines from `output` that appear in `reference`.

    Returns None when nothing matched — lets callers distinguish "no useful
    signal yet" from "deliberately empty filter".
    """
    if not output or not reference:
        return None

    kept: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if len(stripped) < min_line_len:
            continue
        if stripped in reference:
            kept.append(line)

    return "\n".join(kept) if kept else None


def label_triple_from_reference(
    store,
    triple_id: str,
    reference: str,
    *,
    min_line_len: int = 3,
    force: bool = False,
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
        triple.output_data, reference, min_line_len=min_line_len
    )
    if kept is None:
        return None

    store.update_filtered(triple_id, kept)
    return kept
