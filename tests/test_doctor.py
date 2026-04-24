"""Tests for `planckbot doctor` checks and the `demo` CLI command.

Most checks talk to real system state (subprocess, filesystem, env). We
stub the external surface enough to exercise the logic without actually
spawning processes or requiring a real ~/.claude.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from planckbot.doctor import (
    CHECK_FAIL,
    CHECK_OK,
    CHECK_WARN,
    CheckResult,
    _check_binaries,
    _check_claude_config,
    _check_data_dir,
    _check_python_version,
    run_checks,
    worst,
)


# --- worst() --------------------------------------------------------------


def test_worst_with_empty():
    assert worst([]) == CHECK_OK


def test_worst_picks_fail():
    r = [
        CheckResult(CHECK_OK, "a", "b"),
        CheckResult(CHECK_WARN, "c", "d"),
        CheckResult(CHECK_FAIL, "e", "f"),
    ]
    assert worst(r) == CHECK_FAIL


def test_worst_picks_warn_over_ok():
    r = [
        CheckResult(CHECK_OK, "a", "b"),
        CheckResult(CHECK_WARN, "c", "d"),
    ]
    assert worst(r) == CHECK_WARN


# --- _check_python_version ------------------------------------------------


def test_python_version_ok():
    # The test environment runs 3.10+ per CI config; skip if not.
    res = _check_python_version()
    assert res.status == CHECK_OK


# --- _check_claude_config -------------------------------------------------


def test_claude_config_missing(tmp_path, monkeypatch):
    # Point the check at a non-existent path.
    from planckbot import doctor
    monkeypatch.setattr(doctor, "_check_claude_config", doctor._check_claude_config)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    res = _check_claude_config()
    assert res.status == CHECK_WARN
    assert "does not exist" in res.detail
    assert res.fix is not None


def test_claude_config_invalid_json(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude.json").write_text("{ not: 'json'")
    monkeypatch.setenv("HOME", str(fake_home))
    res = _check_claude_config()
    assert res.status == CHECK_FAIL
    assert "not valid JSON" in res.detail


def test_claude_config_has_planckbot_fs(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"planckbot-fs": {"command": "x"}}})
    )
    monkeypatch.setenv("HOME", str(fake_home))
    res = _check_claude_config()
    assert res.status == CHECK_OK
    assert "planckbot-fs" in res.detail


def test_claude_config_unregistered(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}})
    )
    monkeypatch.setenv("HOME", str(fake_home))
    res = _check_claude_config()
    assert res.status == CHECK_WARN


# --- _check_data_dir + _check_binaries ------------------------------------


def test_data_dir_check_ok():
    res = _check_data_dir()
    # Our repo has the data dir set up; either ok or warn. Never fail here
    # unless config truly cannot resolve.
    assert res.status in {CHECK_OK, CHECK_WARN}


def test_binaries_check():
    res = _check_binaries()
    # In an editable install these are present.
    assert res.status in {CHECK_OK, CHECK_FAIL}
    assert "Console scripts" in res.title


# --- run_checks end-to-end ------------------------------------------------


def test_run_checks_returns_one_result_per_check():
    results = run_checks()
    # Each CHECKS entry produces exactly one result; at minimum the
    # baseline ones (python, venv, package, binaries, data dir, db).
    titles = [r.title for r in results]
    assert "Python version" in titles
    assert "DB schema version" in titles
    # Nothing crashes the runner — each result has a defined status.
    for r in results:
        assert r.status in {CHECK_OK, CHECK_WARN, CHECK_FAIL}


# --- demo CLI --------------------------------------------------------------


def test_demo_load_inserts_triples_tagged_demo(conn, monkeypatch):
    """`planckbot demo load` should insert a handful of demo triples all
    tagged with source='demo' so `demo clear` can remove them."""
    from planckbot.cli import cmd_demo
    # Patch _load_stores used by cmd_demo indirectly — cmd_demo opens its
    # own connection via config, so we use PLANCK_DATA_DIR to isolate it.
    # Simpler: monkeypatch get_connection to return our test conn.
    import planckbot.cli as cli_mod
    monkeypatch.setattr(
        "planckbot.cli.get_connection", lambda *_args, **_kw: conn,
        raising=False,
    )
    # Actually cmd_demo imports get_connection lazily from the config
    # module; monkeypatch that instead.
    import planckbot.db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_connection",
                        lambda *_args, **_kw: conn)

    import argparse
    args = argparse.Namespace(action="load")
    rc = cmd_demo(args)
    assert rc == 0
    n = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE source = 'demo'"
    ).fetchone()[0]
    assert n > 0


def test_demo_clear_removes_demo_only(conn, monkeypatch):
    """`demo clear` must only touch source='demo' rows — never real
    proxy:* or manual data."""
    from planckbot.cli import cmd_demo
    import planckbot.db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_connection",
                        lambda *_args, **_kw: conn)

    # Seed: 1 demo, 1 real
    conn.execute(
        "INSERT INTO triples (id, tool_name, input_data, output_data, "
        "source, created_at) VALUES "
        "('d1', 't', '{}', 'x', 'demo', '2026-01-01'),"
        "('r1', 't', '{}', 'x', 'manual', '2026-01-01')"
    )
    conn.commit()

    import argparse
    rc = cmd_demo(argparse.Namespace(action="clear"))
    assert rc == 0

    remaining_sources = [
        r[0] for r in conn.execute("SELECT source FROM triples").fetchall()
    ]
    assert "demo" not in remaining_sources
    assert "manual" in remaining_sources


# --- mcp_status.read_mcp_status -------------------------------------------


def test_read_mcp_status_no_config_returns_error(tmp_path):
    from planckbot.ui.mcp_status import read_mcp_status
    st = read_mcp_status(claude_config=tmp_path / "nonexistent.json")
    assert not st.configured
    assert st.config_error is not None
    assert not st.fs_healthy


def test_read_mcp_status_invalid_json(tmp_path):
    from planckbot.ui.mcp_status import read_mcp_status
    bad = tmp_path / "bad.json"
    bad.write_text("{ bad json")
    st = read_mcp_status(claude_config=bad)
    assert st.config_error is not None
    assert "invalid JSON" in st.config_error


def test_read_mcp_status_parses_fs_args(tmp_path):
    from planckbot.ui.mcp_status import read_mcp_status
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "planckbot-fs": {
                "command": "/bin/planckbot-mcp",
                "args": [
                    "--mode", "intervene",
                    "--threshold", "0.88",
                    "--name", "planckbot-fs",
                    "--",
                    "/bin/npx", "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "/home/user/project",
                ],
            }
        }
    }))
    st = read_mcp_status(claude_config=cfg)
    assert st.configured
    assert st.mode == "intervene"
    assert st.threshold == 0.88
    assert st.upstream_path == "/home/user/project"


def test_read_mcp_status_short_summary_when_unconfigured(tmp_path):
    from planckbot.ui.mcp_status import read_mcp_status
    st = read_mcp_status(claude_config=tmp_path / "x.json")
    assert "not connected" in st.short_summary or "config" in st.short_summary


def test_mcp_status_short_path_shortens_deep_paths(tmp_path):
    from planckbot.ui.mcp_status import MCPStatus
    st = MCPStatus(
        configured=True,
        upstream_path="/home/user/Desktop/PROGRAMACION/myproject",
    )
    short = st._short_path()
    assert short.startswith(".../")
    assert "myproject" in short
