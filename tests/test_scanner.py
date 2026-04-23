"""Tests for the Claude Code conversation-log scanner."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from planckbot.cron.jobs import JobContext, default_registry
from planckbot.cron.scanner import (
    _parse_ts,
    _slugify_cwd,
    extract_assistant_texts,
    scan_project_conversations,
    write_reference_file,
)


@pytest.fixture
def fake_project(tmp_path: Path):
    """Build a fake ~/.claude/projects/<slug>/ tree with one JSONL file.

    Contents exercise every path the scanner cares about: assistant text,
    assistant tool_use (ignored), user (ignored), out-of-window (ignored).
    """
    slug = "-fake-project"
    claude_root = tmp_path / "claude_root"
    proj_dir = claude_root / slug
    proj_dir.mkdir(parents=True)
    log = proj_dir / "session.jsonl"

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=10)).isoformat()
    older = (now - timedelta(days=7)).isoformat()

    lines = [
        # Recent assistant with a text block → should be included
        {
            "type": "assistant",
            "timestamp": recent,
            "message": {"content": [
                {"type": "text", "text": "Keep file A and drop file B."},
            ]},
        },
        # Recent assistant with thinking + text + tool_use → only text kept
        {
            "type": "assistant",
            "timestamp": recent,
            "message": {"content": [
                {"type": "thinking", "thinking": "internal"},
                {"type": "text", "text": "Second useful line."},
                {"type": "tool_use", "id": "x", "name": "Read"},
            ]},
        },
        # Assistant with ONLY a tool_use → no text, nothing to include
        {
            "type": "assistant",
            "timestamp": recent,
            "message": {"content": [{"type": "tool_use", "id": "y"}]},
        },
        # User message → ignored regardless of content
        {
            "type": "user",
            "timestamp": recent,
            "message": {"content": [{"type": "text", "text": "user said X"}]},
        },
        # Old assistant (outside lookback) → ignored
        {
            "type": "assistant",
            "timestamp": older,
            "message": {"content": [{"type": "text", "text": "ancient history"}]},
        },
        # Malformed line → ignored
        "this is not json",
    ]

    with open(log, "w") as f:
        for item in lines:
            if isinstance(item, str):
                f.write(item + "\n")
            else:
                f.write(json.dumps(item) + "\n")

    return {
        "claude_root": claude_root,
        "slug": slug,
        "proj_dir": proj_dir,
    }


def test_slugify_cwd():
    assert _slugify_cwd("/home/kai/proj") == "-home-kai-proj"
    assert _slugify_cwd(Path("/a/b/c")) == "-a-b-c"


def test_parse_ts_handles_z_suffix():
    dt = _parse_ts("2026-04-23T01:10:50.023Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_ts_rejects_garbage():
    assert _parse_ts("") is None
    assert _parse_ts("not-a-date") is None


def test_extract_assistant_texts_filters_to_text_blocks(fake_project):
    log = fake_project["proj_dir"] / "session.jsonl"
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    out = extract_assistant_texts(log, since=since)
    texts = [t for _ts, t in out]
    assert "Keep file A and drop file B." in texts
    assert "Second useful line." in texts
    assert "user said X" not in texts
    assert "ancient history" not in texts
    assert len(out) == 2


def test_extract_skips_items_before_since(fake_project):
    log = fake_project["proj_dir"] / "session.jsonl"
    # Set `since` to 100 days ago — the 7-day-old entry should now appear
    since = datetime.now(timezone.utc) - timedelta(days=100)
    out = extract_assistant_texts(log, since=since)
    assert any(t == "ancient history" for _ts, t in out)


def test_scan_project_conversations_merges_multiple_files(fake_project):
    proj = fake_project["proj_dir"]
    # Add a second JSONL
    second = proj / "another.jsonl"
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    second.write_text(json.dumps({
        "type": "assistant",
        "timestamp": ts,
        "message": {"content": [{"type": "text", "text": "from second file"}]},
    }) + "\n")

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    results = scan_project_conversations(
        fake_project["slug"],
        claude_root=fake_project["claude_root"],
        since=since,
    )
    texts = [t for _ts, t in results]
    assert "from second file" in texts
    assert "Second useful line." in texts


def test_scan_project_respects_max_messages(fake_project):
    # Make 5 recent assistant texts; ask for the latest 2
    proj = fake_project["proj_dir"]
    log = proj / "extra.jsonl"
    now = datetime.now(timezone.utc)
    with open(log, "w") as f:
        for i in range(5):
            ts = (now - timedelta(minutes=5 - i)).isoformat()
            f.write(json.dumps({
                "type": "assistant",
                "timestamp": ts,
                "message": {"content": [{"type": "text", "text": f"msg-{i}"}]},
            }) + "\n")

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    res = scan_project_conversations(
        fake_project["slug"],
        claude_root=fake_project["claude_root"],
        since=since,
        max_messages=2,
    )
    texts = [t for _ts, t in res]
    # We keep the TAIL (most recent) of the list
    assert texts[-1] == "msg-4"
    assert len(texts) == 2


def test_scan_missing_project_returns_empty(tmp_path):
    assert scan_project_conversations(
        "-nonexistent",
        claude_root=tmp_path / "claude_root",
    ) == []


def test_write_reference_file_joins_with_separator(tmp_path):
    out = tmp_path / "ref.txt"
    ts = datetime.now(timezone.utc)
    chars = write_reference_file(out, [(ts, "alpha"), (ts, "beta")])
    body = out.read_text()
    assert "alpha" in body
    assert "beta" in body
    assert "---" in body
    assert chars == len(body)


def test_scan_job_via_registry(fake_project, tmp_path):
    """End-to-end: the job runs through the registry and produces a file."""
    reg = default_registry()
    fn = reg.get("conversation_scanner")
    assert fn is not None

    out_file = tmp_path / "ref.txt"
    ctx = JobContext(
        conn=None,
        params={
            "output_path": str(out_file),
            "project_slug": fake_project["slug"],
            "claude_root": str(fake_project["claude_root"]),
            "lookback_hours": 1,
        },
    )
    result = fn(ctx)
    assert "messages=2" in result
    assert out_file.exists()
    assert "Keep file A" in out_file.read_text()


def test_scan_job_without_output_path_raises():
    reg = default_registry()
    fn = reg.get("conversation_scanner")
    with pytest.raises(ValueError, match="output_path"):
        fn(JobContext(conn=None, params={}))


def test_scan_job_empty_window_returns_status_string(fake_project, tmp_path):
    """If no messages are in the lookback, the job still succeeds with an info
    string — a real-world scenario when the user hasn't used Claude today."""
    reg = default_registry()
    fn = reg.get("conversation_scanner")
    out_file = tmp_path / "ref.txt"
    ctx = JobContext(
        conn=None,
        params={
            "output_path": str(out_file),
            "project_slug": fake_project["slug"],
            "claude_root": str(fake_project["claude_root"]),
            "lookback_hours": 0,  # zero-length window
        },
    )
    result = fn(ctx)
    assert "no assistant messages" in result
    assert not out_file.exists()  # nothing written
