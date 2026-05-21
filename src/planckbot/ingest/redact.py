"""Detect and redact common secret patterns from ingest data.

Applied by the JSONL ingester before triples land in `data/planckbot.db`,
so API keys / private keys / passwords don't end up in plaintext storage or
in LoRA training data downstream. Detected by regex and replaced with
`[REDACTED:<pattern_name>]`. The caller is told which pattern(s) hit so the
triple can be flagged in `context_data.had_secrets`.

Not a security boundary on the proxy path — that's `proxy/ignore.py`. This
runs at storage time on already-emitted JSONL data.
"""

from __future__ import annotations

import re
from typing import Any


# (name, pattern). Field-style patterns (password/api_key) preserve the key
# name and replace only the value so JSON-ish text stays parseable downstream.
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-proj-key", re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9]{40,}")),
    ("github-pat", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("github-oauth", re.compile(r"gho_[A-Za-z0-9]{30,}")),
    ("github-user", re.compile(r"ghu_[A-Za-z0-9]{30,}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "jwt",
        re.compile(
            r"eyJ[A-Za-z0-9_=-]{20,}\.[A-Za-z0-9_=-]{20,}\.[A-Za-z0-9_=-]+"
        ),
    ),
    (
        "password-field",
        re.compile(r'("password"\s*:\s*)"[^"]{4,}"', re.IGNORECASE),
    ),
    (
        "api-key-field",
        re.compile(
            r'("api[_-]?key"\s*:\s*)"[A-Za-z0-9_-]{20,}"',
            re.IGNORECASE,
        ),
    ),
]

_FIELD_PATTERNS = {"password-field", "api-key-field"}


def redact(text: str) -> tuple[str, list[str]]:
    """Replace secret-pattern matches with redaction markers.

    Returns (redacted_text, list of pattern names hit in order). Empty
    string in → empty string out, no hits.
    """
    if not text:
        return text, []
    hits: list[str] = []
    out = text
    for name, pat in SECRET_PATTERNS:
        if name in _FIELD_PATTERNS:
            # Preserve the JSON key, blank only the value.
            new_out, n = pat.subn(
                lambda m, n=name: m.group(1) + f'"[REDACTED:{n}]"', out
            )
        else:
            new_out, n = pat.subn(f"[REDACTED:{name}]", out)
        if n:
            hits.append(name)
            out = new_out
    return out, hits


def redact_obj(obj: Any) -> tuple[Any, list[str]]:
    """Walk a JSON-like object and redact string leaves in place.

    Used for `tool_use.input` which is a dict whose string values may
    contain secrets (e.g. Bash command line with embedded token).
    """
    if isinstance(obj, str):
        text, hits = redact(obj)
        return text, hits
    if isinstance(obj, dict):
        new_d: dict = {}
        all_hits: list[str] = []
        for k, v in obj.items():
            new_v, h = redact_obj(v)
            new_d[k] = new_v
            all_hits.extend(h)
        return new_d, all_hits
    if isinstance(obj, list):
        new_l: list = []
        all_hits = []
        for item in obj:
            new_item, h = redact_obj(item)
            new_l.append(new_item)
            all_hits.extend(h)
        return new_l, all_hits
    return obj, []
