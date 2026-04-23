"""Unified `planckbot` CLI.

Bare `planckbot` still launches the NiceGUI workbench (back-compat). All new
workflows — status, cron, label, train — live under subcommands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


# --- helpers ---------------------------------------------------------------


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def _load_stores():
    """Open DB + hand back the stores the CLI needs.

    Imported lazily so `planckbot --version` / `planckbot --help` stay fast
    and never touch sqlite.
    """
    from planckbot.config import config
    from planckbot.db.engine import get_connection
    from planckbot.cron import CronStore, default_registry
    from planckbot.models.checkpoints import CheckpointManager
    from planckbot.tools.triples import TriplesStore

    conn = get_connection(config.db_path)
    return {
        "conn": conn,
        "config": config,
        "triples": TriplesStore(conn),
        "checkpoints": CheckpointManager(conn),
        "cron": CronStore(conn),
        "cron_registry": default_registry(),
    }


def _resolve_job(cron_store, ref: str):
    """Accept either a job id or a job name."""
    if len(ref) == 36 and ref.count("-") == 4:
        job = cron_store.get(ref)
        if job is not None:
            return job
    return cron_store.by_name(ref)


def _load_script(filename: str):
    """Load a file from ./scripts/ as a one-shot module (no package needed).

    Scripts are not a Python package, so we can't `import` them — but we still
    want the CLI and the scripts to share logic. importlib.util lets us pull
    them in by absolute path whether the CLI runs from the repo or as an
    installed entry-point.
    """
    import importlib.util

    # The CLI ships inside the installed package, so __file__ is under
    # site-packages/planckbot/. We walk up until we find a sibling `scripts/`
    # directory. This lets editable installs (-e .) and source checkouts
    # both work.
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "scripts" / filename,   # src/planckbot/cli.py → repo/scripts
        here.parent.parent / "scripts" / filename,          # fallback
        Path.cwd() / "scripts" / filename,                   # run from repo root
    ]
    for path in candidates:
        if path.exists():
            break
    else:
        raise FileNotFoundError(
            f"cannot locate scripts/{filename} next to the planckbot package"
        )

    spec = importlib.util.spec_from_file_location(
        f"_planckbot_script_{filename[:-3]}", path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- subcommands -----------------------------------------------------------


def cmd_ui(_args) -> int:
    from planckbot.ui.app import start_app
    start_app()
    return 0


def cmd_status(_args) -> int:
    s = _load_stores()
    config = s["config"]
    triples = s["triples"]
    ckpts = s["checkpoints"]
    cron = s["cron"]

    total = triples.count_total()
    counts = triples.count_by_tool()
    savings = triples.token_savings("proxy:intervene")
    jobs = cron.list_all()
    ckpt_rows = ckpts.list_all()

    print(f"PlanckBot — db={config.db_path}")
    print("")
    print(f"  triples total      : {total}")
    print(f"  triples by tool    : "
          + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(none)")
    saved = savings["saved"]
    sign = "+" if saved >= 0 else ""
    pct = (saved / savings["raw_tokens"] * 100) if savings["raw_tokens"] else 0
    print(f"  tokens saved       : {sign}{saved} ({sign}{pct:.0f}%) "
          f"over {savings['intervene_count']} proxy:intervene calls")
    print("")
    print(f"  checkpoints        : {len(ckpt_rows)}")
    for c in ckpt_rows[:5]:
        marker = "★" if c.is_active else " "
        print(f"    {marker} {c.name}  [{c.tool_name or '-'}]  "
              f"{c.adapter_size_mb or 0:.2f} MB  {_fmt_ts(c.created_at)}")
    if len(ckpt_rows) > 5:
        print(f"    … and {len(ckpt_rows) - 5} more")
    print("")
    print(f"  cron jobs          : {len(jobs)}")
    for j in jobs:
        state = "on " if j.enabled else "off"
        last = f"{j.last_status or '-'} @ {_fmt_ts(j.last_run_at)}"
        print(f"    [{state}] {j.name}  type={j.job_type}  "
              f"every={j.interval_seconds}s  last={last}")
    return 0


def cmd_train(args) -> int:
    sys.argv = [
        "train_tool",
        "--tool", args.tool,
        "--fixture", args.fixture,
        "--epochs", str(args.epochs),
        "--timeout-minutes", str(args.timeout_minutes),
    ]
    if args.activate:
        sys.argv.append("--activate")
    _load_script("train_tool.py").main()
    return 0


def cmd_label(args) -> int:
    sys.argv = [
        "auto_label",
        "--tool", args.tool,
        "--recent", str(args.recent),
    ]
    if args.reference:
        sys.argv += ["--reference", args.reference]
    if args.min_line_len:
        sys.argv += ["--min-line-len", str(args.min_line_len)]
    if args.dry_run:
        sys.argv.append("--dry-run")
    if args.force:
        sys.argv.append("--force")
    _load_script("auto_label.py").main()
    return 0


def cmd_proxy_demo(args) -> int:
    mod = _load_script("proxy_demo.py")
    mod.main(args.checkpoint_id)
    return 0


def cmd_preflight(_args) -> int:
    mod = _load_script("mcp_preflight.py")
    import asyncio
    asyncio.run(mod.main())
    return 0


# --- cron subcommands ------------------------------------------------------


def cmd_cron_list(_args) -> int:
    s = _load_stores()
    jobs = s["cron"].list_all()
    if not jobs:
        print("no jobs")
        return 0
    print(f"{'ID':<10} {'NAME':<28} {'TYPE':<12} {'EVERY':>6}  "
          f"{'EN':<3} {'LAST':<10} {'NEXT':<20}")
    for j in jobs:
        print(
            f"{j.id[:8]:<10} {j.name[:28]:<28} {j.job_type:<12} "
            f"{j.interval_seconds:>5}s  "
            f"{('on' if j.enabled else 'off'):<3} "
            f"{(j.last_status or '-'):<10} "
            f"{_fmt_ts(j.next_run_at):<20}"
        )
    return 0


def cmd_cron_add(args) -> int:
    s = _load_stores()
    from planckbot.db.models import CronJob

    if s["cron"].by_name(args.name):
        print(f"a job named {args.name!r} already exists", file=sys.stderr)
        return 1
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(f"--params is not valid JSON: {e}", file=sys.stderr)
        return 2
    job = CronJob(
        name=args.name,
        job_type=args.type,
        params=params,
        interval_seconds=args.interval,
        enabled=0 if args.disabled else 1,
    )
    s["cron"].add(job)
    print(f"added {job.id[:8]} {job.name} (every {job.interval_seconds}s)")
    return 0


def cmd_cron_rm(args) -> int:
    s = _load_stores()
    job = _resolve_job(s["cron"], args.ref)
    if job is None:
        print(f"job not found: {args.ref}", file=sys.stderr)
        return 1
    s["cron"].delete(job.id)
    print(f"removed {job.name}")
    return 0


def cmd_cron_enable(args) -> int:
    return _cron_set_enabled(args, True)


def cmd_cron_disable(args) -> int:
    return _cron_set_enabled(args, False)


def _cron_set_enabled(args, enabled: bool) -> int:
    s = _load_stores()
    job = _resolve_job(s["cron"], args.ref)
    if job is None:
        print(f"job not found: {args.ref}", file=sys.stderr)
        return 1
    s["cron"].set_enabled(job.id, enabled)
    print(f"{'enabled' if enabled else 'disabled'} {job.name}")
    return 0


def cmd_cron_run(args) -> int:
    s = _load_stores()
    from planckbot.cron.daemon import Daemon

    job = _resolve_job(s["cron"], args.ref)
    if job is None:
        print(f"job not found: {args.ref}", file=sys.stderr)
        return 1
    daemon = Daemon(s["conn"], registry=s["cron_registry"])
    status, output = daemon.run_job(job.id)
    print(f"{status}: {output}")
    return 0 if status == "ok" else 1


def cmd_cron_daemon(args) -> int:
    s = _load_stores()
    from planckbot.cron.daemon import Daemon

    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    daemon = Daemon(
        s["conn"],
        tick_seconds=args.tick,
        registry=s["cron_registry"],
        lock_path=args.lock,
    )
    print(f"[cron] daemon up — tick={args.tick}s lock={daemon.lock_path}")
    print("[cron] ctrl-c to stop.")
    try:
        daemon.run()
    except RuntimeError as e:
        print(f"[cron] {e}", file=sys.stderr)
        return 1
    return 0


# --- parser ----------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="planckbot",
        description=(
            "PlanckBot — adaptive tiny-model layer for LLM token optimization."
        ),
    )
    p.add_argument("--version", action="store_true")
    sub = p.add_subparsers(dest="command")

    # ui
    sp = sub.add_parser("ui", help="Launch the NiceGUI workbench.")
    sp.set_defaults(func=cmd_ui)

    # status
    sp = sub.add_parser("status", help="Summarize the current DB state.")
    sp.set_defaults(func=cmd_status)

    # train
    sp = sub.add_parser("train", help="Train a LoRA adapter.")
    sp.add_argument("--tool", required=True)
    sp.add_argument("--fixture", required=True)
    sp.add_argument("--epochs", type=int, default=1)
    sp.add_argument("--timeout-minutes", type=int, default=30)
    sp.add_argument("--activate", action="store_true")
    sp.set_defaults(func=cmd_train)

    # label
    sp = sub.add_parser("label", help="Auto-label unlabeled triples.")
    sp.add_argument("--tool", required=True)
    sp.add_argument("--recent", type=int, default=5)
    sp.add_argument("--reference")
    sp.add_argument("--min-line-len", type=int, default=3)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_label)

    # proxy-demo
    sp = sub.add_parser(
        "proxy-demo", help="Run the proxy demo against a trained adapter.",
    )
    sp.add_argument("checkpoint_id", nargs="?")
    sp.set_defaults(func=cmd_proxy_demo)

    # preflight
    sp = sub.add_parser("preflight", help="Verify planckbot-mcp wraps upstream.")
    sp.set_defaults(func=cmd_preflight)

    # cron <subsub>
    cron_p = sub.add_parser("cron", help="Manage scheduled jobs.")
    cron_sub = cron_p.add_subparsers(dest="cron_command")

    cp = cron_sub.add_parser("list", help="List all jobs.")
    cp.set_defaults(func=cmd_cron_list)

    cp = cron_sub.add_parser("add", help="Create a new job.")
    cp.add_argument("--name", required=True)
    cp.add_argument("--type", required=True,
                    help="Job type (autolabel | retrain | noop).")
    cp.add_argument("--interval", type=int, required=True,
                    help="Seconds between runs.")
    cp.add_argument("--params", default="{}",
                    help="JSON object of job-specific parameters.")
    cp.add_argument("--disabled", action="store_true")
    cp.set_defaults(func=cmd_cron_add)

    cp = cron_sub.add_parser("rm", help="Delete a job by name or id.")
    cp.add_argument("ref")
    cp.set_defaults(func=cmd_cron_rm)

    cp = cron_sub.add_parser("enable", help="Enable a job.")
    cp.add_argument("ref")
    cp.set_defaults(func=cmd_cron_enable)

    cp = cron_sub.add_parser("disable", help="Disable a job.")
    cp.add_argument("ref")
    cp.set_defaults(func=cmd_cron_disable)

    cp = cron_sub.add_parser("run", help="Run a job immediately.")
    cp.add_argument("ref")
    cp.set_defaults(func=cmd_cron_run)

    cp = cron_sub.add_parser("daemon", help="Start the blocking scheduler loop.")
    cp.add_argument("--tick", type=float, default=2.0)
    cp.add_argument(
        "--lock",
        default=os.environ.get("PLANCKBOT_CRON_LOCK", "/tmp/planckbot-cron.lock"),
    )
    cp.set_defaults(func=cmd_cron_daemon)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from planckbot import __version__
        print(f"planckbot {__version__}")
        return 0

    if getattr(args, "command", None) is None:
        # Back-compat: bare `planckbot` launches the UI.
        return cmd_ui(args)

    if args.command == "cron" and getattr(args, "cron_command", None) is None:
        parser.parse_args(["cron", "--help"])
        return 2

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
