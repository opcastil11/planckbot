"""Tests for the unified CLI parser + cron subcommands.

We don't exercise commands that touch training/MCP (those require torch +
subprocess wiring); smoke-test parser wiring and the cron path end-to-end.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest

from planckbot.cli import _build_parser, cmd_cron_add, cmd_cron_list, cmd_cron_run


def test_parser_top_level_help_does_not_crash():
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--help"])


def test_parser_accepts_all_documented_commands():
    p = _build_parser()
    for argv in [
        ["ui"],
        ["status"],
        ["train", "--tool", "t", "--fixture", "f"],
        ["label", "--tool", "t"],
        ["proxy-demo"],
        ["preflight"],
        ["cron", "list"],
        ["cron", "add", "--name", "n", "--type", "noop", "--interval", "60"],
        ["cron", "rm", "n"],
        ["cron", "enable", "n"],
        ["cron", "disable", "n"],
        ["cron", "run", "n"],
        ["cron", "daemon"],
    ]:
        args = p.parse_args(argv)
        assert hasattr(args, "func"), f"no func for argv={argv}"


def test_cron_add_then_list_end_to_end(monkeypatch, conn, capsys):
    # Point the CLI's sqlite connection at the test DB.
    import planckbot.cli as cli_module
    monkeypatch.setattr(
        cli_module, "_load_stores",
        lambda: {
            "conn": conn,
            "config": type("C", (), {"db_path": ":memory:"})(),
            "triples": None,
            "checkpoints": None,
            "cron": __import__(
                "planckbot.cron.store", fromlist=["CronStore"]
            ).CronStore(conn),
            "cron_registry": __import__(
                "planckbot.cron.jobs", fromlist=["default_registry"]
            ).default_registry(),
        },
    )
    # add
    args = _build_parser().parse_args([
        "cron", "add", "--name", "n1", "--type", "noop",
        "--interval", "120", "--params", '{"message": "hi"}',
    ])
    assert cmd_cron_add(args) == 0
    # list
    list_args = _build_parser().parse_args(["cron", "list"])
    assert cmd_cron_list(list_args) == 0

    out = capsys.readouterr().out
    assert "n1" in out
    assert "noop" in out


def test_cron_run_dispatches_job(monkeypatch, conn, capsys):
    from planckbot.cron.jobs import default_registry
    from planckbot.cron.store import CronStore
    from planckbot.db.models import CronJob

    store = CronStore(conn)
    store.add(CronJob(
        name="r1", job_type="noop",
        interval_seconds=60, params={"message": "from_cli"},
    ))

    import planckbot.cli as cli_module
    monkeypatch.setattr(
        cli_module, "_load_stores",
        lambda: {
            "conn": conn,
            "config": type("C", (), {"db_path": ":memory:"})(),
            "triples": None,
            "checkpoints": None,
            "cron": store,
            "cron_registry": default_registry(),
        },
    )

    args = _build_parser().parse_args(["cron", "run", "r1"])
    rc = cmd_cron_run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "ok" in out
    assert "from_cli" in out


def test_cron_add_rejects_duplicate_name(monkeypatch, conn, capsys):
    from planckbot.cron.jobs import default_registry
    from planckbot.cron.store import CronStore
    from planckbot.db.models import CronJob

    store = CronStore(conn)
    store.add(CronJob(name="dup", job_type="noop", interval_seconds=60))

    import planckbot.cli as cli_module
    monkeypatch.setattr(
        cli_module, "_load_stores",
        lambda: {
            "conn": conn,
            "config": type("C", (), {"db_path": ":memory:"})(),
            "triples": None,
            "checkpoints": None,
            "cron": store,
            "cron_registry": default_registry(),
        },
    )
    args = _build_parser().parse_args([
        "cron", "add", "--name", "dup", "--type", "noop", "--interval", "60",
    ])
    rc = cmd_cron_add(args)
    assert rc == 1


def test_cron_add_rejects_invalid_params_json(monkeypatch, conn, capsys):
    from planckbot.cron.jobs import default_registry
    from planckbot.cron.store import CronStore
    import planckbot.cli as cli_module

    store = CronStore(conn)
    monkeypatch.setattr(
        cli_module, "_load_stores",
        lambda: {
            "conn": conn,
            "config": type("C", (), {"db_path": ":memory:"})(),
            "triples": None,
            "checkpoints": None,
            "cron": store,
            "cron_registry": default_registry(),
        },
    )
    args = _build_parser().parse_args([
        "cron", "add", "--name", "bad", "--type", "noop",
        "--interval", "60", "--params", "not-json",
    ])
    rc = cmd_cron_add(args)
    assert rc == 2
