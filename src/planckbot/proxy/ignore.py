"""`.mcpignore` — path filtering at the proxy layer.

The upstream filesystem MCP server has no notion of ignore lists; it
exposes whatever it's told to serve. Our proxy sits in between, so we
can refuse calls on sensitive paths before they reach the upstream (and
therefore before any secret content gets recorded as a triple).

Two things happen here:

1. When a tool call carries an absolute path argument (`read_text_file`,
   `read_file`, `get_file_info`, …), we match that path against the
   ignore patterns. If it matches, the proxy returns a structured
   refusal to Claude and never calls the upstream. The file content
   never leaves the upstream process.

2. When a tool call returns a listing (`list_directory`,
   `list_directory_with_sizes`, `directory_tree`, `search_files`), we
   let the upstream respond, then strip matching entries from the
   response before handing it to Claude.

Patterns are standard fnmatch globs, applied to the last path component
AND to the full path. A pattern ending in `/` matches directories; we
then also drop anything under a matched directory.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path


# Patterns we always deny regardless of user config. These are the
# canonical "please don't read my secrets" surfaces. Users can loosen
# them only by writing custom logic — not by editing .mcpignore.
HARD_DEFAULTS: tuple[str, ...] = (
    # Dotenv family
    ".env",
    ".env.*",
    "*.env",
    "*.env.*",
    # Keys / certs
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.crt",
    "*.cer",
    # SSH / GPG
    "id_rsa*",
    "id_ed25519*",
    "id_dsa*",
    "id_ecdsa*",
    "*.pub",         # public keys also leak useful attacker info (username, host)
    ".ssh/",
    ".gnupg/",
    # Cloud credentials
    ".aws/credentials",
    ".aws/config",
    ".azure/",
    ".gcloud/",
    # Netrc / git
    ".netrc",
    "_netrc",
    ".git/config",
    # Common secret dumps
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    ".secrets",
    "service-account*.json",
    # Kubernetes / docker
    ".kube/config",
    ".docker/config.json",
)


@dataclass
class IgnoreRules:
    patterns: tuple[str, ...]
    custom_path: Path | None = None     # path of the user's .mcpignore if any

    def matches(self, path: str) -> str | None:
        """Return the first matching pattern (truthy) or None."""
        if not path:
            return None
        p = Path(path)
        name = p.name
        # Walk path parts so a dir match below (e.g. `.ssh/`) catches
        # everything under it.
        parts = list(p.parts)
        for pat in self.patterns:
            if pat.endswith("/"):
                dir_name = pat[:-1]
                # match any path part exactly equal to dir_name
                if any(part == dir_name for part in parts):
                    return pat
            else:
                # basename match
                if fnmatch.fnmatchcase(name, pat):
                    return pat
                # full-path match ending with pattern
                if fnmatch.fnmatchcase(path, "*/" + pat) or \
                   fnmatch.fnmatchcase(path, pat):
                    return pat
        return None


def load_rules(served_path: str | Path | None) -> IgnoreRules:
    """Assemble the final ruleset: HARD_DEFAULTS + any `.mcpignore` in the
    root of the served path.

    `.mcpignore` format is one-pattern-per-line, `#` comments supported,
    blank lines ignored. Patterns match against basenames + full paths
    via fnmatch. A trailing `/` marks a directory pattern (drops whole
    subtree).
    """
    patterns: list[str] = list(HARD_DEFAULTS)
    custom = None
    if served_path:
        root = Path(served_path)
        candidate = root / ".mcpignore"
        if candidate.exists() and candidate.is_file():
            custom = candidate
            for raw in candidate.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    return IgnoreRules(patterns=tuple(patterns), custom_path=custom)


def filter_listing(
    text: str, base_dir: str | None, rules: IgnoreRules,
) -> tuple[str, int]:
    """Strip entries that match the rules from a `list_directory`-style
    text output.

    The filesystem MCP server emits lines like:
        [DIR] node_modules
        [FILE] package.json
    We strip any line whose subject matches the rules. `base_dir` (the
    path being listed) is used to construct a full path for matching,
    so a pattern `.env` catches `.env` when listing `/project`.

    Returns (filtered_text, num_redacted).
    """
    kept: list[str] = []
    redacted = 0
    base = Path(base_dir) if base_dir else None
    for line in text.splitlines():
        stripped = line.strip()
        # Extract the entry name from `[DIR] name` or `[FILE] name`, else
        # treat the whole line as a path.
        name = stripped
        for prefix in ("[DIR]", "[FILE]", "[LINK]"):
            if stripped.startswith(prefix):
                name = stripped[len(prefix):].strip()
                break
        full = str(base / name) if base else name
        if rules.matches(full) or rules.matches(name):
            redacted += 1
            continue
        kept.append(line)
    return "\n".join(kept), redacted


# Which tool names we consider "read-a-specific-path" vs "list-a-directory".
# The names are the UPSTREAM tool names (unnamespaced) as advertised by
# @modelcontextprotocol/server-filesystem.
READ_SPECIFIC_TOOLS: frozenset[str] = frozenset({
    "read_file", "read_text_file", "read_media_file",
    "get_file_info",
})
LISTING_TOOLS: frozenset[str] = frozenset({
    "list_directory", "list_directory_with_sizes",
    "directory_tree", "search_files",
})


def path_arg_of(tool_name: str, arguments: dict) -> str | None:
    """Extract the single path-like argument a 'read X at path' tool
    carries, if any. Returns None for non-path tools."""
    if tool_name not in READ_SPECIFIC_TOOLS:
        return None
    return arguments.get("path")


def base_arg_of(tool_name: str, arguments: dict) -> str | None:
    """Extract the base directory from a listing tool's args, if any."""
    if tool_name not in LISTING_TOOLS:
        return None
    return arguments.get("path") or arguments.get("root")
