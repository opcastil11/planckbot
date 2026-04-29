"""Tests for ClaudeCodeJsonlSource — pairs tool_use+tool_result from Claude
Code's per-project JSONL transcripts into triples."""

from __future__ import annotations

import json
from pathlib import Path

from planckbot.cron.jobs import default_registry, JobContext
from planckbot.ingest import ClaudeCodeJsonlSource
from planckbot.tools.projects import ProjectStore


def _make_jsonl(
    path: Path,
    entries: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _wrap_use(
    *,
    use_id: str,
    name: str,
    input_data: dict,
    timestamp: str = "2026-04-29T10:00:00.000Z",
    session_id: str = "sess-1",
    msg_uuid: str = "msg-use",
) -> dict:
    return {
        "type": "assistant",
        "uuid": msg_uuid,
        "timestamp": timestamp,
        "sessionId": session_id,
        "cwd": "/home/u/proj",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": use_id, "name": name, "input": input_data},
            ],
        },
    }


def _wrap_result(
    *,
    use_id: str,
    output: str | list,
    timestamp: str = "2026-04-29T10:00:01.000Z",
    is_error: bool = False,
    session_id: str = "sess-1",
    msg_uuid: str = "msg-res",
) -> dict:
    blk = {"type": "tool_result", "tool_use_id": use_id, "content": output}
    if is_error:
        blk["is_error"] = True
    return {
        "type": "user",
        "uuid": msg_uuid,
        "timestamp": timestamp,
        "sessionId": session_id,
        "message": {"role": "user", "content": [blk]},
    }


# --- core flow -------------------------------------------------------------


def test_pairs_tool_use_with_result(tmp_path, triples_store):
    project_path = "/home/u/myproj"
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "session-1.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(
            use_id="t_1", name="Read",
            input_data={"file_path": "/x/y.py"},
        ),
        _wrap_result(use_id="t_1", output="contents of y.py"),
    ])

    src = ClaudeCodeJsonlSource(project_path, claude_root=tmp_path)
    n = src.ingest(triples_store)
    assert n == 1

    [triple] = triples_store.list_all()
    assert triple.tool_name == "Read"
    assert triple.source == "claude_code:jsonl"
    assert triple.session_id == "sess-1"
    assert "contents of y.py" in triple.output_data
    ctx = json.loads(triple.context_data)
    assert ctx["tool_use_id"] == "t_1"
    assert ctx["use_ts"] == "2026-04-29T10:00:00.000Z"


def test_dedup_on_rerun(tmp_path, triples_store):
    """Re-running over the same JSONL must not create duplicates."""
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(use_id="t_a", name="Bash", input_data={"command": "ls"}),
        _wrap_result(use_id="t_a", output="a.py"),
    ])

    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    assert src.ingest(triples_store) == 1
    # Second run: same source, same JSONL, expect 0 new.
    src2 = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    assert src2.ingest(triples_store) == 0
    assert triples_store.count_total() == 1


def test_skips_unpaired_blocks(tmp_path, triples_store):
    """A tool_use with no matching tool_result is dropped (and vice versa)."""
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(use_id="paired", name="Read", input_data={"path": "a"}),
        _wrap_result(use_id="paired", output="ok"),
        _wrap_use(use_id="orphan_use", name="Read", input_data={"path": "b"}),
        _wrap_result(use_id="orphan_result", output="???"),
    ])

    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    assert src.ingest(triples_store) == 1
    [t] = triples_store.list_all()
    ctx = json.loads(t.context_data)
    assert ctx["tool_use_id"] == "paired"


def test_skips_errored_results_by_default(tmp_path, triples_store):
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(use_id="bad", name="Bash", input_data={"command": "oops"}),
        _wrap_result(use_id="bad", output="not found", is_error=True),
        _wrap_use(use_id="good", name="Read", input_data={"path": "x"}),
        _wrap_result(use_id="good", output="ok"),
    ])

    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    assert src.ingest(triples_store) == 1

    src2 = ClaudeCodeJsonlSource(
        "/home/u/myproj", claude_root=tmp_path, skip_errors=False,
    )
    assert src2.ingest(triples_store) == 1  # the bad one this time
    assert triples_store.count_total() == 2


def test_walks_multiple_jsonl_files(tmp_path, triples_store):
    slug = "-home-u-myproj"
    _make_jsonl(tmp_path / slug / "a.jsonl", [
        _wrap_use(use_id="from_a", name="Read", input_data={"p": "x"}),
        _wrap_result(use_id="from_a", output="X"),
    ])
    _make_jsonl(tmp_path / slug / "b.jsonl", [
        _wrap_use(use_id="from_b", name="Edit", input_data={"p": "y"}),
        _wrap_result(use_id="from_b", output="Y"),
    ])

    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    assert src.ingest(triples_store) == 2
    names = {t.tool_name for t in triples_store.list_all()}
    assert names == {"Read", "Edit"}


def test_handles_string_and_list_result_content(tmp_path, triples_store):
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(use_id="str_r", name="A", input_data={"p": 1}),
        _wrap_result(use_id="str_r", output="plain string"),
        _wrap_use(use_id="list_r", name="B", input_data={"p": 2}),
        _wrap_result(use_id="list_r", output=[
            {"type": "text", "text": "first line"},
            {"type": "text", "text": "second line"},
        ]),
    ])
    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    assert src.ingest(triples_store) == 2
    out_by_tool = {t.tool_name: t.output_data for t in triples_store.list_all()}
    assert out_by_tool["A"] == "plain string"
    assert "first line\nsecond line" == out_by_tool["B"]


def test_preserves_original_timestamp_as_created_at(tmp_path, triples_store):
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(
            use_id="t1", name="Read", input_data={"p": "x"},
            timestamp="2026-04-22T10:00:00.000Z",
        ),
        _wrap_result(use_id="t1", output="ok"),
    ])
    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    src.ingest(triples_store)
    [t] = triples_store.list_all()
    assert t.created_at == "2026-04-22T10:00:00.000Z"


def test_respects_limit(tmp_path, triples_store):
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    entries = []
    for i in range(5):
        ts = f"2026-04-29T10:00:0{i}.000Z"
        entries.append(_wrap_use(
            use_id=f"t_{i}", name="Read",
            input_data={"p": str(i)}, timestamp=ts,
        ))
        entries.append(_wrap_result(use_id=f"t_{i}", output=f"o{i}"))
    _make_jsonl(jsonl, entries)

    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    assert src.ingest(triples_store, limit=3) == 3


def test_filters_by_since(tmp_path, triples_store):
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(
            use_id="old", name="Read",
            input_data={"p": "a"}, timestamp="2026-04-20T10:00:00.000Z",
        ),
        _wrap_result(use_id="old", output="o"),
        _wrap_use(
            use_id="new", name="Read",
            input_data={"p": "b"}, timestamp="2026-04-25T10:00:00.000Z",
        ),
        _wrap_result(use_id="new", output="o"),
    ])
    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    n = src.ingest(triples_store, since="2026-04-22T00:00:00.000Z")
    assert n == 1


def test_missing_project_dir_yields_nothing(tmp_path, triples_store):
    src = ClaudeCodeJsonlSource("/home/u/does-not-exist", claude_root=tmp_path)
    assert src.ingest(triples_store) == 0


def test_tags_project_id_when_passed(tmp_path, triples_store, conn):
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(use_id="t1", name="Read", input_data={"p": "x"}),
        _wrap_result(use_id="t1", output="ok"),
    ])
    pstore = ProjectStore(conn)
    proj = pstore.create(name="myproj", path="/home/u/myproj")
    src = ClaudeCodeJsonlSource("/home/u/myproj", claude_root=tmp_path)
    src.ingest(triples_store, project_id=proj.id)
    [t] = triples_store.list_all()
    assert t.project_id == proj.id


# --- cron job integration --------------------------------------------------


def test_cron_job_resolves_active_project(tmp_path, conn):
    slug = "-home-u-myproj"
    jsonl = tmp_path / slug / "s.jsonl"
    _make_jsonl(jsonl, [
        _wrap_use(use_id="t1", name="Bash", input_data={"command": "ls"}),
        _wrap_result(use_id="t1", output="a.py"),
    ])
    proj = ProjectStore(conn).create(
        name="myproj", path="/home/u/myproj", activate=True,
    )

    reg = default_registry()
    fn = reg.get("claude_code_ingest")
    assert fn is not None
    ctx = JobContext(
        conn=conn,
        params={"claude_root": str(tmp_path)},
        project_id=proj.id,
    )
    msg = fn(ctx)
    assert "ingested=1" in msg

    from planckbot.tools.triples import TriplesStore
    [t] = TriplesStore(conn).list_all()
    assert t.project_id == proj.id
    assert t.tool_name == "Bash"


def test_cron_job_requires_path_or_project(conn):
    reg = default_registry()
    fn = reg.get("claude_code_ingest")
    ctx = JobContext(conn=conn, params={}, project_id=None)
    try:
        fn(ctx)
    except ValueError as e:
        assert "project" in str(e).lower()
    else:
        assert False, "expected ValueError"
