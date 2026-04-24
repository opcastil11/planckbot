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
    _tool_name_matches,
    extract_assistant_texts,
    find_response_after_tool_call,
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


# --- per-triple linker ----------------------------------------------------


def test_tool_name_matches_exact():
    assert _tool_name_matches("list_directory", "list_directory")


def test_tool_name_matches_namespaced():
    assert _tool_name_matches(
        "mcp__planckbot-fs__list_directory", "list_directory"
    )


def test_tool_name_matches_rejects_unrelated():
    assert not _tool_name_matches("read_file", "list_directory")
    assert not _tool_name_matches("mcp__other__list_directory_x",
                                   "list_directory")


def _mk_log(tmp_path: Path, entries: list[dict]) -> Path:
    log = tmp_path / "session.jsonl"
    with open(log, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return log


def test_find_response_picks_message_after_matching_tool_use(tmp_path):
    now = datetime.now(timezone.utc)
    log = _mk_log(tmp_path, [
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=5)).isoformat(),
            "message": {"content": [
                {"type": "thinking", "thinking": "going to list"},
                {"type": "tool_use", "id": "t1",
                 "name": "mcp__planckbot-fs__list_directory",
                 "input": {"path": "/repo"}},
            ]},
        },
        {
            "type": "user",
            "timestamp": (now - timedelta(seconds=4)).isoformat(),
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "raw output"}]},
            ]},
        },
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=3)).isoformat(),
            "message": {"content": [
                {"type": "text",
                 "text": "Keep src and package.json; drop the rest."},
            ]},
        },
    ])
    out = find_response_after_tool_call(
        log,
        tool_name="list_directory",
        input_data={"path": "/repo"},
        near_ts=now,
    )
    assert out == "Keep src and package.json; drop the rest."


def test_find_response_disambiguates_by_input(tmp_path):
    """Two calls to the same tool close in time — must pick the right one."""
    now = datetime.now(timezone.utc)
    log = _mk_log(tmp_path, [
        # Call 1: path=/a
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=20)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": "t1",
                 "name": "mcp__planckbot-fs__list_directory",
                 "input": {"path": "/a"}},
            ]},
        },
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=18)).isoformat(),
            "message": {"content": [
                {"type": "text", "text": "A had items X, Y"},
            ]},
        },
        # Call 2: path=/b
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=10)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": "t2",
                 "name": "mcp__planckbot-fs__list_directory",
                 "input": {"path": "/b"}},
            ]},
        },
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=8)).isoformat(),
            "message": {"content": [
                {"type": "text", "text": "B has FOO and BAR"},
            ]},
        },
    ])
    out_a = find_response_after_tool_call(
        log, tool_name="list_directory",
        input_data={"path": "/a"},
        near_ts=now - timedelta(seconds=20),
    )
    out_b = find_response_after_tool_call(
        log, tool_name="list_directory",
        input_data={"path": "/b"},
        near_ts=now - timedelta(seconds=10),
    )
    assert "X, Y" in out_a
    assert "FOO and BAR" in out_b


def test_find_response_returns_none_when_no_match(tmp_path):
    now = datetime.now(timezone.utc)
    log = _mk_log(tmp_path, [
        {
            "type": "assistant",
            "timestamp": now.isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "read_file",
                 "input": {"path": "/x"}},
            ]},
        },
    ])
    assert find_response_after_tool_call(
        log, tool_name="list_directory",
        input_data={"path": "/x"}, near_ts=now,
    ) is None


def test_find_response_respects_drift_window(tmp_path):
    now = datetime.now(timezone.utc)
    log = _mk_log(tmp_path, [
        {
            "type": "assistant",
            "timestamp": (now - timedelta(hours=3)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": "t1",
                 "name": "list_directory",
                 "input": {"path": "/repo"}},
            ]},
        },
        {
            "type": "assistant",
            "timestamp": (now - timedelta(hours=3) + timedelta(seconds=2)).isoformat(),
            "message": {"content": [
                {"type": "text", "text": "ancient response"},
            ]},
        },
    ])
    # Default max_drift is 120s; this tool call is 3h old → should miss
    assert find_response_after_tool_call(
        log, tool_name="list_directory",
        input_data={"path": "/repo"}, near_ts=now,
    ) is None


def test_find_response_skips_tool_use_only_messages(tmp_path):
    """The NEXT assistant message after the tool call may itself be another
    tool_use (chained calls). Skip those until we find one with text."""
    now = datetime.now(timezone.utc)
    log = _mk_log(tmp_path, [
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=10)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "list_directory",
                 "input": {"path": "/r"}},
            ]},
        },
        # Middle message has only a tool_use (chain) — no text
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=8)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": "t2", "name": "read_file",
                 "input": {"path": "/r/f"}},
            ]},
        },
        # Finally, a text message
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=5)).isoformat(),
            "message": {"content": [
                {"type": "text", "text": "The important file is package.json"},
            ]},
        },
    ])
    out = find_response_after_tool_call(
        log, tool_name="list_directory",
        input_data={"path": "/r"}, near_ts=now,
    )
    assert "package.json" in out


def test_autolabel_precise_job_labels_from_next_assistant(tmp_path, conn):
    """End-to-end: a triple + a matching JSONL log → cron job labels it
    using ONLY the next-message text, ignoring distractors elsewhere."""
    from planckbot.cron.jobs import JobContext, default_registry
    from planckbot.tools.triples import TriplesStore

    # 1) Seed a triple for tool=list_directory with a known output
    now = datetime.now(timezone.utc)
    store = TriplesStore(conn)
    triple = store.add(
        tool_name="list_directory",
        input_data={"path": "/repo"},
        output_data=(
            "[DIR] .git\n[DIR] src\n[DIR] node_modules\n"
            "[FILE] package.json\n[FILE] README.md"
        ),
        source="proxy:observe",
    )
    # Force the timestamp so it lines up with the fake JSONL
    conn.execute(
        "UPDATE triples SET created_at = ? WHERE id = ?",
        ((now - timedelta(seconds=3)).isoformat(), triple.id),
    )
    conn.commit()

    # 2) Seed the Claude Code JSONL log
    slug = "-fake-orquesta"
    claude_root = tmp_path / "claude_root"
    proj_dir = claude_root / slug
    proj_dir.mkdir(parents=True)
    _mk_log(proj_dir, [
        # A *distractor* earlier assistant message that mentions `.git` —
        # this is the kind of content that poisons the batch autolabel.
        {
            "type": "assistant",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "message": {"content": [
                {"type": "text",
                 "text": "Earlier I had mentioned [DIR] .git in passing."},
            ]},
        },
        # The matching tool_use + response
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=4)).isoformat(),
            "message": {"content": [
                {"type": "tool_use", "id": "t1",
                 "name": "mcp__planckbot-fs__list_directory",
                 "input": {"path": "/repo"}},
            ]},
        },
        {
            "type": "assistant",
            "timestamp": (now - timedelta(seconds=2)).isoformat(),
            "message": {"content": [
                {"type": "text",
                 "text": "The important ones are [DIR] src and [FILE] package.json."},
            ]},
        },
    ])

    # 3) Run the job
    reg = default_registry()
    fn = reg.get("autolabel_precise")
    out = fn(JobContext(conn=conn, params={
        "tool": "list_directory",
        "project_slug": slug,
        "claude_root": str(claude_root),
        "max_drift_seconds": 60,
    }))
    assert "labeled=1" in out

    # 4) Verify filtered_output is ONLY what appeared in the NEXT message,
    #    NOT `.git` (which was in the earlier distractor)
    reloaded = store.get(triple.id)
    assert reloaded.filtered_output is not None
    assert "[DIR] src" in reloaded.filtered_output
    assert "[FILE] package.json" in reloaded.filtered_output
    assert "[DIR] .git" not in reloaded.filtered_output


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
