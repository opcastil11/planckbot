"""Tests for schema v6 — projects table + ProjectStore + per-project scoping.

Covers:
  * v6 migration from a fresh DB (tables, columns, indexes).
  * ProjectStore CRUD + invariants (one-active, path absolute, rename, delete).
  * Per-project scoping on TriplesStore / CheckpointManager / CronStore /
    SynthesizedToolStore / GapReportStore.
  * Proxy tags recorded triples with the supplied project_id.
  * adopt_legacy re-parents every pre-v6 NULL-project row.
  * CLI helper `_rewrite_mcp_upstream_path` rewrites the served path in
    a ~/.claude.json shaped like `planckbot init` emits.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from planckbot.db.engine import get_connection
from planckbot.db.migrations import SCHEMA_VERSION
from planckbot.db.models import CronJob, ModelCheckpoint, SynthesizedTool
from planckbot.models.checkpoints import CheckpointManager
from planckbot.cron.store import CronStore
from planckbot.proxy.intercept import ObserveMode, PlanckProxy
from planckbot.synth.detector import GapReportStore, build_gap_reports
from planckbot.synth.detector import SequenceMatch
from planckbot.synth.meta import SynthesizedToolStore
from planckbot.tools.projects import ProjectStore
from planckbot.tools.triples import TriplesStore


# --- schema / migration ----------------------------------------------------


def test_schema_version_is_v6(conn):
    row = conn.execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()
    assert row[0] == SCHEMA_VERSION == 6


def test_projects_table_exists(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='projects'"
    )
    assert cur.fetchone() is not None


def test_project_id_columns_added(conn):
    for table in (
        "triples",
        "model_checkpoints",
        "cron_jobs",
        "synthesized_tools",
        "gap_reports",
        "experiments",
    ):
        cols = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        assert "project_id" in cols, f"{table}.project_id missing"


def test_v6_indexes_present(conn):
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_projects_active" in names
    assert "idx_triples_project" in names
    assert "idx_checkpoints_project" in names
    assert "idx_cron_project" in names
    assert "idx_synth_project" in names
    assert "idx_gap_project" in names


def test_migration_from_v5(tmp_path):
    """Simulate a pre-v6 DB and upgrade in place.

    We write the minimum schema needed to look like a v5 install, stamp
    `schema_version=5`, drop in a couple of rows, then open via
    get_connection which should apply _upgrade_to_v6 and leave the old
    data intact with NULL project_id.
    """
    db = tmp_path / "pre_v6.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        CREATE TABLE triples (
            id TEXT PRIMARY KEY, tool_name TEXT NOT NULL,
            session_id TEXT, input_data TEXT NOT NULL,
            context_data TEXT, output_data TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER,
            filtered_output TEXT, filtered_tokens INTEGER,
            source TEXT DEFAULT 'manual', created_at TEXT NOT NULL,
            experiment_id TEXT, tool_version_id TEXT
        );
        CREATE TABLE model_checkpoints (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, base_model TEXT NOT NULL,
            model_size_mb REAL, adapter_path TEXT, adapter_size_mb REAL,
            experiment_id TEXT, strategy TEXT, tool_name TEXT,
            tool_version_id TEXT, lora_config TEXT, training_args TEXT,
            num_triples INTEGER, eval_metrics TEXT, is_active INTEGER DEFAULT 0,
            blessed INTEGER NOT NULL DEFAULT 0, tuned_threshold REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE cron_jobs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
            interval_seconds INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, last_run_at TEXT,
            next_run_at TEXT, last_status TEXT, last_output TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE synthesized_tools (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            input_schema TEXT NOT NULL DEFAULT '{}',
            code TEXT NOT NULL, source_file_path TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL, created_by TEXT, gap_report_id TEXT
        );
        CREATE TABLE gap_reports (
            id TEXT PRIMARY KEY, tool_sequence TEXT NOT NULL,
            occurrences INTEGER NOT NULL,
            example_triple_ids TEXT NOT NULL,
            proposed_name TEXT, proposed_description TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );
        CREATE TABLE experiments (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            hypothesis TEXT, experiment_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            config TEXT NOT NULL DEFAULT '{}', random_seed INTEGER,
            data_snapshot TEXT, checkpoint_id TEXT,
            created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
            metrics TEXT, observations TEXT
        );
        INSERT INTO schema_version (version) VALUES (5);
        INSERT INTO triples (id, tool_name, input_data, output_data, created_at)
            VALUES ('t1', 'list_dir', '{}', 'x', '2025-01-01');
        """
    )
    c.commit()
    c.close()

    # Now re-open via the public API — migrations should bring it to v6.
    c2 = get_connection(db)
    cur = c2.execute("SELECT MAX(version) FROM schema_version")
    assert cur.fetchone()[0] == SCHEMA_VERSION
    # Pre-existing row survives with NULL project_id.
    row = c2.execute(
        "SELECT tool_name, project_id FROM triples WHERE id = 't1'"
    ).fetchone()
    assert row["tool_name"] == "list_dir"
    assert row["project_id"] is None
    c2.close()


# --- ProjectStore CRUD -----------------------------------------------------


def test_create_requires_absolute_path(conn):
    ps = ProjectStore(conn)
    with pytest.raises(ValueError, match="absolute"):
        ps.create(name="p", path="relative/path")


def test_create_rejects_duplicate_name(conn, tmp_path):
    ps = ProjectStore(conn)
    ps.create(name="p", path=str(tmp_path))
    with pytest.raises(ValueError, match="already exists"):
        ps.create(name="p", path=str(tmp_path))


def test_set_active_enforces_single(conn, tmp_path):
    ps = ProjectStore(conn)
    a = ps.create(name="a", path=str(tmp_path / "a"))
    b = ps.create(name="b", path=str(tmp_path / "b"))
    ps.set_active(a.id)
    assert ps.get_active().id == a.id
    ps.set_active(b.id)
    act = ps.get_active()
    assert act.id == b.id
    # a must have been deactivated
    assert ps.get(a.id).is_active == 0


def test_create_with_activate_deactivates_others(conn, tmp_path):
    ps = ProjectStore(conn)
    a = ps.create(name="a", path=str(tmp_path / "a"), activate=True)
    b = ps.create(name="b", path=str(tmp_path / "b"), activate=True)
    # Only b should be active now
    actives = [p for p in ps.list_all() if p.is_active]
    assert len(actives) == 1
    assert actives[0].id == b.id


def test_by_path_lookup(conn, tmp_path):
    ps = ProjectStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    assert ps.by_path(str(tmp_path)).id == p.id
    assert ps.by_path(str(tmp_path / "nope")) is None


def test_rename(conn, tmp_path):
    ps = ProjectStore(conn)
    p = ps.create(name="old", path=str(tmp_path))
    ps.rename(p.id, "new")
    assert ps.by_name("new").id == p.id
    assert ps.by_name("old") is None


def test_rename_rejects_conflict(conn, tmp_path):
    ps = ProjectStore(conn)
    a = ps.create(name="a", path=str(tmp_path / "a"))
    ps.create(name="b", path=str(tmp_path / "b"))
    with pytest.raises(ValueError):
        ps.rename(a.id, "b")


def test_delete_non_cascade_reparents_to_null(conn, tmp_path):
    ps = ProjectStore(conn)
    ts = TriplesStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    ts.add("list_dir", {"path": "/"}, "x", project_id=p.id)
    ps.delete(p.id)
    # Triple survives but is now legacy (project_id=NULL) so the FK
    # constraint stays satisfied after the project row disappears.
    assert ts.count_total() == 1
    assert ts.list_all()[0].project_id is None


def test_delete_cascade_wipes_triples(conn, tmp_path):
    ps = ProjectStore(conn)
    ts = TriplesStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    ts.add("list_dir", {"path": "/"}, "x", project_id=p.id)
    ps.delete(p.id, cascade=True)
    assert ts.count_total() == 0


# --- per-project scoping on stores -----------------------------------------


def test_triples_filter_by_project(conn, tmp_path):
    ps = ProjectStore(conn)
    ts = TriplesStore(conn)
    a = ps.create(name="a", path=str(tmp_path / "a"))
    b = ps.create(name="b", path=str(tmp_path / "b"))
    ts.add("list_dir", {}, "A1", project_id=a.id)
    ts.add("list_dir", {}, "A2", project_id=a.id)
    ts.add("read_file", {}, "B1", project_id=b.id)
    ts.add("orphan", {}, "X")  # NULL project_id

    assert ts.count_total() == 4
    assert ts.count_total(project_id=a.id) == 2
    assert ts.count_total(project_id=b.id) == 1
    assert ts.count_by_tool(project_id=a.id) == {"list_dir": 2}
    assert ts.count_by_tool(project_id=b.id) == {"read_file": 1}
    assert len(ts.list_all(project_id=a.id)) == 2
    assert len(ts.get_by_tool("list_dir", project_id=a.id)) == 2
    assert len(ts.get_by_tool("list_dir", project_id=b.id)) == 0


def test_triples_token_savings_by_project(conn, tmp_path):
    ps = ProjectStore(conn)
    ts = TriplesStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    # One intervene triple tagged to p, one without project, one unfiltered.
    ts.add(
        "list_dir", {}, "aaaa bbbb cccc",
        source="proxy:intervene",
        filtered_output="aaaa",
        project_id=p.id,
    )
    ts.add(
        "list_dir", {}, "aaaa bbbb cccc",
        source="proxy:intervene",
        filtered_output="aaaa",
    )
    sav_all = ts.token_savings()
    sav_p = ts.token_savings(project_id=p.id)
    assert sav_all["intervene_count"] == 2
    assert sav_p["intervene_count"] == 1


def test_checkpoint_list_by_project(conn, tmp_path):
    ps = ProjectStore(conn)
    cm = CheckpointManager(conn)
    a = ps.create(name="a", path=str(tmp_path / "a"))
    b = ps.create(name="b", path=str(tmp_path / "b"))
    cm.save(ModelCheckpoint(
        name="ck-a", base_model="x", tool_name="list_dir", project_id=a.id,
    ))
    cm.save(ModelCheckpoint(
        name="ck-b", base_model="x", tool_name="list_dir", project_id=b.id,
    ))
    assert cm.count() == 2
    assert cm.count(project_id=a.id) == 1
    assert cm.count(project_id=b.id) == 1
    assert len(cm.list_all(project_id=a.id)) == 1
    assert cm.list_all(project_id=a.id)[0].name == "ck-a"


def test_checkpoint_active_scope(conn, tmp_path):
    """Activating a checkpoint for project A must not deactivate project B's
    active checkpoint for the same tool."""
    ps = ProjectStore(conn)
    cm = CheckpointManager(conn)
    a = ps.create(name="a", path=str(tmp_path / "a"))
    b = ps.create(name="b", path=str(tmp_path / "b"))
    ck_a = cm.save(ModelCheckpoint(
        name="ck-a", base_model="x", tool_name="list_dir",
        project_id=a.id, blessed=1,
    ))
    ck_b = cm.save(ModelCheckpoint(
        name="ck-b", base_model="x", tool_name="list_dir",
        project_id=b.id, blessed=1,
    ))
    cm.activate(ck_a.id)
    cm.activate(ck_b.id)
    # Both should now be active — different projects.
    assert cm.get(ck_a.id).is_active == 1
    assert cm.get(ck_b.id).is_active == 1
    # get_active with project filter returns the right one.
    assert cm.get_active("list_dir", project_id=a.id).id == ck_a.id
    assert cm.get_active("list_dir", project_id=b.id).id == ck_b.id


def test_cron_list_by_project_includes_global(conn, tmp_path):
    ps = ProjectStore(conn)
    cs = CronStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    cs.add(CronJob(
        name="global", job_type="noop", interval_seconds=60,
    ))
    cs.add(CronJob(
        name="scoped", job_type="noop", interval_seconds=60,
        project_id=p.id,
    ))
    # When filtering by project, global (NULL) jobs should still appear.
    names = {j.name for j in cs.list_all(project_id=p.id)}
    assert names == {"global", "scoped"}


def test_synth_list_by_project(conn, tmp_path):
    ps = ProjectStore(conn)
    ss = SynthesizedToolStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    ss.insert(SynthesizedTool(
        name="global_tool", code="def global_tool(): pass", status="active",
    ))
    ss.insert(SynthesizedTool(
        name="p_tool", code="def p_tool(): pass", status="active",
        project_id=p.id,
    ))
    active = {t.name for t in ss.list_active(project_id=p.id)}
    assert active == {"global_tool", "p_tool"}


def test_gap_reports_by_project(conn, tmp_path):
    ps = ProjectStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    matches = [SequenceMatch(
        sequence=("a", "b"), occurrences=2, example_triple_ids=["x", "y"],
    )]
    build_gap_reports(conn, matches, project_id=p.id)
    reports = GapReportStore(conn).list_all(project_id=p.id)
    assert len(reports) == 1
    assert reports[0].project_id == p.id


# --- proxy wiring ----------------------------------------------------------


def test_proxy_tags_triples_with_project(conn, tmp_path):
    ps = ProjectStore(conn)
    ts = TriplesStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    proxy = PlanckProxy(
        store=ts, mode=ObserveMode(), project_id=p.id,
    )

    def fake_tool(path):
        return f"contents of {path}"

    proxy.call(fake_tool, "read_file", path="/tmp/x")
    triples = ts.list_all(project_id=p.id)
    assert len(triples) == 1
    assert triples[0].project_id == p.id


def test_proxy_without_project_keeps_null(conn, tmp_path):
    ts = TriplesStore(conn)
    proxy = PlanckProxy(store=ts, mode=ObserveMode())
    proxy.call(lambda: "x", "noop")
    assert ts.list_all()[0].project_id is None


# --- adopt_legacy ----------------------------------------------------------


def test_adopt_legacy(conn, tmp_path):
    ts = TriplesStore(conn)
    # Insert two legacy (NULL-project) triples BEFORE any project exists.
    ts.add("list_dir", {}, "x")
    ts.add("read_file", {}, "y")

    ps = ProjectStore(conn)
    p = ps.create(name="p", path=str(tmp_path))
    touched = ps.adopt_legacy(p.id)

    assert touched["triples"] == 2
    assert ts.count_total(project_id=p.id) == 2


# --- CLI: ~/.claude.json rewrite -------------------------------------------


def test_rewrite_mcp_upstream_path(tmp_path, monkeypatch):
    """The helper flips the last positional arg on planckbot-fs's args list."""
    from planckbot import cli

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    claude_json = fake_home / ".claude.json"
    # Shape matches what `planckbot init` emits (last arg = served path).
    claude_json.write_text(json.dumps({
        "mcpServers": {
            "planckbot-fs": {
                "command": "/fake/planckbot-mcp",
                "args": [
                    "--mode", "observe", "--name", "planckbot-fs", "--",
                    "/usr/bin/npx", "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "/old/path",
                ],
            },
            "planckbot-synth": {"command": "/fake/planckbot-synth"},
        }
    }))

    ok, msg = cli._rewrite_mcp_upstream_path("/new/path")
    assert ok, msg

    updated = json.loads(claude_json.read_text())
    args = updated["mcpServers"]["planckbot-fs"]["args"]
    assert args[-1] == "/new/path"
    # Other entries untouched.
    assert updated["mcpServers"]["planckbot-synth"]["command"] == (
        "/fake/planckbot-synth"
    )


def test_rewrite_mcp_noop_if_missing(tmp_path, monkeypatch):
    from planckbot import cli
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    ok, msg = cli._rewrite_mcp_upstream_path("/x")
    assert not ok
    assert "does not exist" in msg


# --- CLI: project-scope MCP mirror -----------------------------------------


def test_sync_mcp_entries_creates_project_section():
    """Helper adds the entries under cfg.projects[upstream].mcpServers,
    creating the project section if absent."""
    from planckbot import cli
    cfg = {"mcpServers": {}}
    entries = {"planckbot-fs": {"command": "/fake/fs"}}
    changed = cli._sync_mcp_entries_to_project_scope(cfg, "/some/path", entries)
    assert changed
    assert cfg["projects"]["/some/path"]["mcpServers"] == entries


def test_sync_mcp_entries_fills_empty_override():
    """The real-world bug: Claude Code wrote `mcpServers: {}` into the
    project section, shadowing global entries. The helper must fill the
    empty dict rather than leave it in place."""
    from planckbot import cli
    cfg = {
        "mcpServers": {"planckbot-fs": {"command": "/global/fs"}},
        "projects": {"/p": {"mcpServers": {}, "hasTrustDialogAccepted": True}},
    }
    entries = {"planckbot-fs": {"command": "/global/fs"}}
    changed = cli._sync_mcp_entries_to_project_scope(cfg, "/p", entries)
    assert changed
    assert cfg["projects"]["/p"]["mcpServers"] == entries
    # Must not clobber unrelated project fields.
    assert cfg["projects"]["/p"]["hasTrustDialogAccepted"] is True


def test_sync_mcp_entries_idempotent():
    from planckbot import cli
    cfg = {"projects": {"/p": {"mcpServers": {"planckbot-fs": {"command": "/x"}}}}}
    entries = {"planckbot-fs": {"command": "/x"}}
    assert not cli._sync_mcp_entries_to_project_scope(cfg, "/p", entries)


def test_rewrite_mcp_upstream_path_mirrors_into_new_project_scope(
    tmp_path, monkeypatch,
):
    """Switching to a folder that already has an empty project-scoped
    `mcpServers: {}` must populate it, not just the global."""
    from planckbot import cli
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    claude_json = fake_home / ".claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {
            "planckbot-fs": {
                "command": "/fake/planckbot-mcp",
                "args": [
                    "--mode", "observe", "--name", "planckbot-fs", "--",
                    "/usr/bin/npx", "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "/old/path",
                ],
            },
            "planckbot-synth": {"command": "/fake/planckbot-synth"},
        },
        "projects": {
            "/new/path": {"mcpServers": {}, "hasTrustDialogAccepted": True},
        },
    }))

    ok, msg = cli._rewrite_mcp_upstream_path("/new/path")
    assert ok, msg

    updated = json.loads(claude_json.read_text())
    # Global scope: path is rewritten.
    assert updated["mcpServers"]["planckbot-fs"]["args"][-1] == "/new/path"
    # Project scope: both servers got mirrored, overriding the empty
    # dict that was hiding them.
    proj_mcp = updated["projects"]["/new/path"]["mcpServers"]
    assert set(proj_mcp.keys()) == {"planckbot-fs", "planckbot-synth"}
    assert proj_mcp["planckbot-fs"]["args"][-1] == "/new/path"


def test_rewrite_mcp_changes_when_only_project_scope_needs_fix(
    tmp_path, monkeypatch,
):
    """If the global scope already points at the new path but the project
    scope is still empty, the helper must still write the file — the
    missing project-scope mirror is itself a change worth persisting."""
    from planckbot import cli
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    claude_json = fake_home / ".claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {
            "planckbot-fs": {
                "command": "/fake/planckbot-mcp",
                "args": [
                    "--mode", "observe", "--name", "planckbot-fs", "--",
                    "/usr/bin/npx", "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "/p",
                ],
            },
        },
        "projects": {"/p": {"mcpServers": {}}},
    }))

    ok, msg = cli._rewrite_mcp_upstream_path("/p")
    assert ok, msg
    updated = json.loads(claude_json.read_text())
    assert "planckbot-fs" in updated["projects"]["/p"]["mcpServers"]
