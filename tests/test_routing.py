"""Tests for tools.routing — per-project tool-routing preferences.

Covers the full mode cycle (off → soft → hard → soft → off), confirms
that our edits to the user's CLAUDE.md and .claude/settings.json are
reversible without clobbering pre-existing content, and that the
sidecar-tracked injected_deny list correctly preserves entries the user
put there themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planckbot.tools import routing


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


# --- detection ------------------------------------------------------------


def test_get_mode_defaults_to_off(project):
    assert routing.get_mode(project) == "off"


def test_get_mode_detects_soft_from_claude_md(project):
    (project / "CLAUDE.md").write_text(
        "some preexisting content\n\n" + routing._CLAUDE_MD_BLOCK + "\n"
    )
    assert routing.get_mode(project) == "soft"


def test_get_mode_detects_hard_from_sidecar(project):
    (project / ".claude").mkdir()
    (project / ".claude" / ".planckbot-routing.json").write_text(
        json.dumps({"mode": "hard", "injected_deny": ["Read"]})
    )
    # Even without the CLAUDE.md block, sidecar wins.
    assert routing.get_mode(project) == "hard"


# --- transitions ----------------------------------------------------------


def test_off_to_soft_creates_claude_md(project):
    routing.set_mode(project, "soft")
    md = (project / "CLAUDE.md").read_text()
    assert routing._SENTINEL_BEGIN in md
    assert routing._SENTINEL_END in md
    assert routing.get_mode(project) == "soft"


def test_soft_to_off_removes_block_and_leaves_empty_md_deleted(project):
    routing.set_mode(project, "soft")
    assert (project / "CLAUDE.md").exists()
    routing.set_mode(project, "off")
    # Nothing else was in CLAUDE.md, so removing the block deletes the file.
    assert not (project / "CLAUDE.md").exists()
    assert routing.get_mode(project) == "off"


def test_soft_to_off_preserves_user_claude_md_content(project):
    user_content = "# My project\n\nSome notes the user wrote.\n"
    (project / "CLAUDE.md").write_text(user_content)
    routing.set_mode(project, "soft")
    routing.set_mode(project, "off")
    # User content intact, no PlanckBot sentinels left.
    remaining = (project / "CLAUDE.md").read_text()
    assert user_content.strip() == remaining.strip()
    assert routing._SENTINEL_BEGIN not in remaining


def test_off_to_hard_writes_settings_and_sidecar(project):
    routing.set_mode(project, "hard")
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    assert set(settings["permissions"]["deny"]) == set(
        routing.NATIVE_READ_TOOLS
    )
    sidecar = json.loads(
        (project / ".claude" / ".planckbot-routing.json").read_text()
    )
    assert sidecar["mode"] == "hard"
    assert set(sidecar["injected_deny"]) == set(routing.NATIVE_READ_TOOLS)
    assert routing.get_mode(project) == "hard"


def test_hard_to_off_removes_everything(project):
    routing.set_mode(project, "hard")
    routing.set_mode(project, "off")
    assert not (project / ".claude" / "settings.json").exists()
    assert not (project / ".claude" / ".planckbot-routing.json").exists()
    assert not (project / "CLAUDE.md").exists()
    assert routing.get_mode(project) == "off"


def test_hard_to_off_preserves_user_deny_entries(project):
    """User denied WebFetch manually. Our hard→off must not remove it."""
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["WebFetch"]}})
    )
    routing.set_mode(project, "hard")
    deny_after_apply = json.loads(
        (project / ".claude" / "settings.json").read_text()
    )["permissions"]["deny"]
    assert "WebFetch" in deny_after_apply
    for tool in routing.NATIVE_READ_TOOLS:
        assert tool in deny_after_apply

    routing.set_mode(project, "off")
    settings = json.loads(
        (project / ".claude" / "settings.json").read_text()
    )
    assert settings["permissions"]["deny"] == ["WebFetch"]


def test_hard_to_off_preserves_user_read_deny_if_they_had_it(project):
    """If user independently denied 'Read' before we applied hard, we must
    NOT remove it when downgrading — only entries we actually injected
    (tracked in sidecar) get removed."""
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Read"]}})
    )
    routing.set_mode(project, "hard")
    sidecar = json.loads(
        (project / ".claude" / ".planckbot-routing.json").read_text()
    )
    # Read was not injected by us, only Glob + Grep.
    assert set(sidecar["injected_deny"]) == {"Glob", "Grep"}

    routing.set_mode(project, "off")
    settings = json.loads(
        (project / ".claude" / "settings.json").read_text()
    )
    assert settings["permissions"]["deny"] == ["Read"]


def test_soft_to_hard_keeps_claude_md(project):
    routing.set_mode(project, "soft")
    md_before = (project / "CLAUDE.md").read_text()
    routing.set_mode(project, "hard")
    md_after = (project / "CLAUDE.md").read_text()
    assert md_before == md_after
    assert routing.get_mode(project) == "hard"


def test_hard_to_soft_only_removes_settings(project):
    routing.set_mode(project, "hard")
    routing.set_mode(project, "soft")
    assert (project / "CLAUDE.md").exists()
    assert not (project / ".claude" / "settings.json").exists()
    assert not (project / ".claude" / ".planckbot-routing.json").exists()
    assert routing.get_mode(project) == "soft"


def test_set_mode_is_idempotent(project):
    routing.set_mode(project, "hard")
    md_before = (project / "CLAUDE.md").read_text()
    settings_before = (project / ".claude" / "settings.json").read_text()
    # Apply again — nothing should change.
    routing.set_mode(project, "hard")
    assert (project / "CLAUDE.md").read_text() == md_before
    assert (project / ".claude" / "settings.json").read_text() == settings_before


def test_set_mode_rejects_unknown(project):
    with pytest.raises(ValueError, match="unknown routing mode"):
        routing.set_mode(project, "bogus")  # type: ignore[arg-type]


def test_set_mode_rejects_non_directory(tmp_path):
    bogus = tmp_path / "not-a-dir"
    with pytest.raises(ValueError, match="not a directory"):
        routing.set_mode(bogus, "soft")
