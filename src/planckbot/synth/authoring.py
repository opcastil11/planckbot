"""LLM-authored Layer D synthesis.

Given a gap_report (a sequence of tools that repeats often), produce a
Python function body for a merged tool. Two paths:

  1. If `ANTHROPIC_API_KEY` is set, call the Anthropic Messages API with
     the gap report's example triples as context. The model returns a
     Python function body which goes through the same AST gate as
     `synthesize_tool`.

  2. Otherwise, fall back to a deterministic template generator that
     emits a minimal "call each tool in sequence, concatenate results"
     function. Not optimal, but it's always available and lets the
     Layer-D loop close end-to-end on offline installs.

The code here doesn't MUTATE the DB; it returns a code string + metadata
for the caller (CLI or UI) to feed into `synthesize_tool`.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from planckbot.db.models import GapReport, Triple


PROMPT_TEMPLATE = """You are writing a Python function for PlanckBot, a system
that generates merged tools from repeated call patterns.

Repeated sequence: {sequence}
Occurrences: {occurrences}

Example calls (input → output) from the triples database:

{examples}

Write a single Python function `{name}` that takes the UNION of inputs seen
across these tools and returns a merged / summarized output that preserves
the information Claude actually cited in each example. Rules:

- The function signature must be `def {name}({params}) -> str:`.
- Import only from Python's stdlib.
- No `eval`, `exec`, `subprocess`, `os.system`, `shutil.rmtree`, `pickle`.
- Output a single string (JSON-encode structured data yourself).
- Prefer compression over verbosity — the whole point is to save tokens.

Respond with ONLY the function definition, no explanation, no markdown."""


@dataclass
class AuthoredTool:
    name: str
    description: str
    input_schema: dict
    code: str
    source: str         # 'llm' | 'template'
    usage_tokens: int | None = None


def _collect_examples(
    conn: sqlite3.Connection, triple_ids: list[str], limit: int = 3,
) -> list[Triple]:
    """Fetch up to `limit` example triples for the gap report."""
    examples = []
    for tid in triple_ids[:limit]:
        row = conn.execute(
            "SELECT * FROM triples WHERE id = ?", (tid,)
        ).fetchone()
        if row is None:
            continue
        examples.append(Triple.from_row(row))
    return examples


def _summarize_examples(examples: list[Triple]) -> str:
    """Compact textual rendering of example triples for the LLM prompt."""
    parts = []
    for i, t in enumerate(examples, 1):
        parts.append(
            f"Example {i} — tool={t.tool_name}\n"
            f"  input: {(t.input_data or '')[:200]}\n"
            f"  output (first 300 chars): {(t.output_data or '')[:300]}\n"
        )
    return "\n".join(parts)


def _template_body(name: str, sequence: list[str]) -> str:
    """Fallback generator when no LLM is available.

    Emits a pure-stdlib stub that takes `path` as its only parameter and
    returns a JSON payload combining the tool names in the sequence. Not
    useful in production, but it's safe, valid Python, and exercises the
    full Layer D pipeline so `planckbot synth create` can be tested
    offline.
    """
    return (
        f'"""Auto-generated template — replace with a real implementation.\n'
        f'Sequence: {" → ".join(sequence)}\n'
        f'"""\n'
        f'import json\n\n\n'
        f'def {name}(path: str) -> str:\n'
        f'    return json.dumps({{\n'
        f'        "auto_generated": True,\n'
        f'        "sequence": {sequence!r},\n'
        f'        "path": path,\n'
        f'        "note": "replace this body with a real merged '
        f'implementation",\n'
        f'    }})\n'
    )


def author_tool_for_gap(
    gap: GapReport,
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    model: str = "claude-sonnet-4-6",
    use_api: bool | None = None,
) -> AuthoredTool:
    """Produce Python code for a new tool that fuses `gap.tool_sequence`.

    Args:
        gap: a GapReport row (typically `status='accepted'`)
        conn: SQLite connection to fetch example triples
        name: override the proposed_name
        model: Anthropic model id to use when API is available
        use_api: force API on/off; None → auto-detect via ANTHROPIC_API_KEY

    Returns an AuthoredTool with `source='llm'` or `source='template'`.
    """
    fn_name = (name or gap.proposed_name
               or "_".join(gap.tool_sequence) + "_combined")
    description = (
        gap.proposed_description
        or f"Merged tool for sequence: {' → '.join(gap.tool_sequence)}"
    )
    # Default input schema: single `path` string — most common case for
    # filesystem-shaped tools. Can be refined per-gap later.
    input_schema = {"path": "string"}

    examples = _collect_examples(conn, gap.example_triple_ids)

    if use_api is None:
        use_api = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if use_api and examples:
        try:
            code, usage = _call_anthropic(
                fn_name, gap, examples, model=model,
            )
            return AuthoredTool(
                name=fn_name,
                description=description,
                input_schema=input_schema,
                code=code,
                source="llm",
                usage_tokens=usage,
            )
        except Exception:
            # Fall through to template on any API failure. The caller
            # should check `source` if they want to know.
            pass

    return AuthoredTool(
        name=fn_name,
        description=description,
        input_schema=input_schema,
        code=_template_body(fn_name, gap.tool_sequence),
        source="template",
    )


def _call_anthropic(
    fn_name: str, gap: GapReport, examples: list[Triple],
    *, model: str,
) -> tuple[str, int]:
    """Call the Anthropic API. Returns (code_string, total_tokens_used).

    We keep this logic inline rather than importing `anthropic` at
    module top because the package is optional. If it's not installed
    the ImportError bubbles and the caller falls back to template.
    """
    from anthropic import Anthropic   # type: ignore

    prompt = PROMPT_TEMPLATE.format(
        sequence=" → ".join(gap.tool_sequence),
        occurrences=gap.occurrences,
        examples=_summarize_examples(examples),
        name=fn_name,
        params="path: str",
    )
    client = Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    ).strip()
    # Strip markdown fences if the model wrapped them around the code.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("python"):
            text = text[len("python"):]
        text = text.strip()
    usage = (msg.usage.input_tokens + msg.usage.output_tokens
             if hasattr(msg, "usage") else 0)
    return text, usage
