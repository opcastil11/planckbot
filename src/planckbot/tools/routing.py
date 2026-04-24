"""Per-project tool-routing preferences.

Lets the user choose how aggressively a project steers Claude Code away
from its native filesystem tools (`Read`/`Glob`/`Grep`) and toward the
`mcp__planckbot-fs__*` equivalents, so calls flow through the proxy and
become triples.

Three modes:

    off   — nothing managed. Claude picks whatever it wants (default).
    soft  — a sentineled block in the project's CLAUDE.md asks Claude
            to prefer `mcp__planckbot-fs__*` for filesystem ops. Claude
            usually honors CLAUDE.md instructions, but it's a nudge
            not a guarantee.
    hard  — soft + deny rules in `.claude/settings.json` that remove
            the native read tools from Claude's menu. It has to go
            through the MCP. We only add entries that aren't already
            in the user's deny list and record what we injected in
            `.claude/.planckbot-routing.json` so removal is precise.

All state lives in files inside the project folder — this module owns
no database rows. `get_mode()` detects state by inspecting those files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


Mode = Literal["off", "soft", "hard"]
VALID_MODES: tuple[Mode, ...] = ("off", "soft", "hard")

# Native filesystem tools we ask Claude Code to skip when hard mode is on.
# We intentionally do NOT touch Write/Edit/Bash — those either have no
# MCP equivalent (Bash) or are write-side (and the proxy only optimizes
# reads today).
NATIVE_READ_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")

_SENTINEL_BEGIN = "<!-- planckbot:routing begin -->"
_SENTINEL_END = "<!-- planckbot:routing end -->"

_CLAUDE_MD_BLOCK = f"""{_SENTINEL_BEGIN}
## Tool routing (managed by PlanckBot)

For filesystem operations in this project, prefer the
`mcp__planckbot-fs__*` tools over the native `Read`, `Glob`, and `Grep`
tools. This routes calls through PlanckBot so they can be observed,
labeled, and eventually compressed.

If a task genuinely needs native tools (e.g. large recursive greps),
use them — this is a nudge, not a rule.
{_SENTINEL_END}"""


@dataclass
class RoutingState:
    mode: Mode
    claude_md_present: bool
    deny_entries: list[str]   # entries PlanckBot added to permissions.deny


def _claude_md(project_path: Path) -> Path:
    return project_path / "CLAUDE.md"


def _settings_json(project_path: Path) -> Path:
    return project_path / ".claude" / "settings.json"


def _sidecar(project_path: Path) -> Path:
    return project_path / ".claude" / ".planckbot-routing.json"


# --- CLAUDE.md block -------------------------------------------------------


def _strip_block(text: str) -> str:
    """Remove the PlanckBot sentineled block from a CLAUDE.md string."""
    begin = text.find(_SENTINEL_BEGIN)
    if begin == -1:
        return text
    end = text.find(_SENTINEL_END, begin)
    if end == -1:
        return text
    end += len(_SENTINEL_END)
    # Also eat a trailing newline so we don't leave a blank line behind.
    if end < len(text) and text[end] == "\n":
        end += 1
    # And eat a leading newline so we don't leave a blank line above.
    if begin > 0 and text[begin - 1] == "\n":
        begin -= 1
    return text[:begin] + text[end:]


def _apply_claude_md(project_path: Path) -> None:
    """Ensure the sentineled block is present in CLAUDE.md. Appends at end.
    If CLAUDE.md doesn't exist, creates it containing only the block."""
    path = _claude_md(project_path)
    if path.exists():
        text = path.read_text()
        if _SENTINEL_BEGIN in text and _SENTINEL_END in text:
            return  # already applied, no-op
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        path.write_text(text + sep + _CLAUDE_MD_BLOCK + "\n")
    else:
        path.write_text(_CLAUDE_MD_BLOCK + "\n")


def _remove_claude_md(project_path: Path) -> None:
    path = _claude_md(project_path)
    if not path.exists():
        return
    new_text = _strip_block(path.read_text())
    # If removing our block emptied the file, delete it — we don't leave
    # phantom CLAUDE.md files behind.
    if new_text.strip() == "":
        path.unlink()
    else:
        path.write_text(new_text)


def _claude_md_has_block(project_path: Path) -> bool:
    path = _claude_md(project_path)
    if not path.exists():
        return False
    text = path.read_text()
    return _SENTINEL_BEGIN in text and _SENTINEL_END in text


# --- settings.json permissions --------------------------------------------


def _load_settings(project_path: Path) -> dict:
    path = _settings_json(project_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        # Malformed file — leave it alone. Caller should bail out.
        raise ValueError(f"{path} is not valid JSON")


def _save_settings(project_path: Path, cfg: dict) -> None:
    path = _settings_json(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if cfg == {}:
        # Don't leave an empty file behind; remove it so we're a no-op.
        if path.exists():
            path.unlink()
        return
    path.write_text(json.dumps(cfg, indent=2) + "\n")


def _load_sidecar(project_path: Path) -> dict:
    path = _sidecar(project_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _save_sidecar(project_path: Path, data: dict) -> None:
    path = _sidecar(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _remove_sidecar(project_path: Path) -> None:
    path = _sidecar(project_path)
    if path.exists():
        path.unlink()


def _apply_hard_deny(project_path: Path) -> list[str]:
    """Merge NATIVE_READ_TOOLS into settings.json's permissions.deny.
    Records which tools we added (not already present) in the sidecar.
    Returns the list of tools actually injected this call (may be empty
    if already applied)."""
    cfg = _load_settings(project_path)
    perms = cfg.setdefault("permissions", {})
    deny = perms.setdefault("deny", [])
    already = _load_sidecar(project_path).get("injected_deny", [])
    # Anything we previously injected should stay injected; anything new
    # that the user added manually stays untouched.
    injected = list(already)
    for tool in NATIVE_READ_TOOLS:
        if tool not in deny:
            deny.append(tool)
            if tool not in injected:
                injected.append(tool)
    perms["deny"] = deny
    cfg["permissions"] = perms
    _save_settings(project_path, cfg)
    _save_sidecar(project_path, {"mode": "hard", "injected_deny": injected})
    return injected


def _remove_hard_deny(project_path: Path) -> list[str]:
    """Undo _apply_hard_deny using the sidecar as the truth source of
    what we injected. Returns the list of tools removed from deny."""
    sidecar = _load_sidecar(project_path)
    injected = sidecar.get("injected_deny", [])
    if not injected:
        _remove_sidecar(project_path)
        return []
    try:
        cfg = _load_settings(project_path)
    except ValueError:
        _remove_sidecar(project_path)
        return []
    perms = cfg.get("permissions", {})
    deny = perms.get("deny", [])
    removed = []
    for tool in injected:
        if tool in deny:
            deny.remove(tool)
            removed.append(tool)
    if deny:
        perms["deny"] = deny
        cfg["permissions"] = perms
    else:
        perms.pop("deny", None)
        if perms:
            cfg["permissions"] = perms
        else:
            cfg.pop("permissions", None)
    _save_settings(project_path, cfg)
    _remove_sidecar(project_path)
    return removed


# --- public API ------------------------------------------------------------


def get_mode(project_path: Path) -> Mode:
    """Detect the current mode by looking at files in the project."""
    has_md = _claude_md_has_block(project_path)
    sidecar_mode = _load_sidecar(project_path).get("mode")
    if sidecar_mode == "hard":
        return "hard"
    if has_md:
        return "soft"
    return "off"


def set_mode(project_path: Path, mode: Mode) -> RoutingState:
    """Transition the project to `mode`, idempotently.

    Returns the resulting state so the caller can show a receipt.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unknown routing mode: {mode!r}")
    project_path = Path(project_path)
    if not project_path.is_dir():
        raise ValueError(f"{project_path} is not a directory")

    current = get_mode(project_path)
    if mode == current:
        # No-op, but still report an accurate state.
        return RoutingState(
            mode=current,
            claude_md_present=_claude_md_has_block(project_path),
            deny_entries=_load_sidecar(project_path).get("injected_deny", []),
        )

    # Normalize to off first when downgrading, so we always leave a clean
    # state before re-applying.
    if current == "hard" and mode != "hard":
        _remove_hard_deny(project_path)
    if current != "off" and mode == "off":
        _remove_claude_md(project_path)

    if mode == "soft":
        if current == "off":
            _apply_claude_md(project_path)
        # If current was "hard", CLAUDE.md block is already there.
    elif mode == "hard":
        if not _claude_md_has_block(project_path):
            _apply_claude_md(project_path)
        _apply_hard_deny(project_path)

    return RoutingState(
        mode=mode,
        claude_md_present=_claude_md_has_block(project_path),
        deny_entries=_load_sidecar(project_path).get("injected_deny", []),
    )
