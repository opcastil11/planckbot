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


def _ui_running(port: int, *, timeout: float = 0.5) -> bool:
    """True iff something is listening on 127.0.0.1:port. We probe loopback
    instead of the configured bind host because the UI may bind 0.0.0.0 but
    is always reachable via loopback when running on this machine."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _load_stores():
    """Open DB + hand back the stores the CLI needs.

    Imported lazily so `planckbot --version` / `planckbot --help` stay fast
    and never touch sqlite.
    """
    from planckbot.config import config
    from planckbot.db.engine import get_connection
    from planckbot.cron import CronStore, default_registry
    from planckbot.models.checkpoints import CheckpointManager
    from planckbot.tools.projects import ProjectStore
    from planckbot.tools.triples import TriplesStore

    conn = get_connection(config.db_path)
    return {
        "conn": conn,
        "config": config,
        "triples": TriplesStore(conn),
        "checkpoints": CheckpointManager(conn),
        "cron": CronStore(conn),
        "cron_registry": default_registry(),
        "projects": ProjectStore(conn),
    }


def _claude_json_path() -> Path:
    return Path.home() / ".claude.json"


def _sync_mcp_entries_to_project_scope(
    cfg: dict, upstream_path: str, entries: dict
) -> bool:
    """Mirror planckbot-* MCP entries into the per-project scope.

    Claude Code writes `mcpServers: {}` into every project section on first
    open of that folder. That empty dict is treated as an override of the
    global `mcpServers`, so globally-registered servers are silently hidden
    in any folder the user has ever opened with Claude Code. Mirror the
    entries into `cfg.projects[upstream_path].mcpServers` so the MCP is
    discoverable regardless of which scope Claude Code consults first.

    Creates the project section if absent — Claude Code will merge its own
    defaults on top of the bare `{"mcpServers": {...}}` block the next
    time it opens in that folder. Returns True if anything changed.
    """
    if not entries:
        return False
    projects = cfg.setdefault("projects", {})
    proj = projects.setdefault(upstream_path, {})
    proj_mcp = proj.setdefault("mcpServers", {})
    changed = False
    for name, spec in entries.items():
        if proj_mcp.get(name) != spec:
            proj_mcp[name] = spec
            changed = True
    return changed


def _rewrite_mcp_upstream_path(new_path: str) -> tuple[bool, str]:
    """Rewrite the planckbot-fs MCP entry in ~/.claude.json so its last
    positional arg (the served folder) becomes `new_path`.

    Returns (changed, message). No-op if the file doesn't exist or the
    entry isn't present — in that case the caller can suggest `planckbot
    init` to wire it up. Does NOT restart Claude Code; the user sees the
    new path next time they relaunch.
    """
    claude_json = _claude_json_path()
    if not claude_json.exists():
        return (False, f"{claude_json} does not exist — run `planckbot init`")
    try:
        cfg = json.loads(claude_json.read_text())
    except json.JSONDecodeError as e:
        return (False, f"{claude_json} is malformed: {e}")

    servers = cfg.get("mcpServers") or {}
    fs = servers.get("planckbot-fs")
    if not fs or not isinstance(fs.get("args"), list) or not fs["args"]:
        return (
            False,
            "~/.claude.json has no planckbot-fs entry; run `planckbot init`",
        )

    args = list(fs["args"])
    # The init command builds args as:
    #   ["--mode", MODE, "--name", "planckbot-fs", "--",
    #    NPX, "-y", "@modelcontextprotocol/server-filesystem", PATH]
    # So the served path is always the LAST arg. Replace it in place.
    path_changed = args[-1] != new_path
    args[-1] = new_path
    fs["args"] = args
    servers["planckbot-fs"] = fs
    cfg["mcpServers"] = servers

    # Also mirror the (now-correct) servers into the new project's scope,
    # otherwise the switch is invisible to any Claude Code session opened
    # in `new_path`.
    mirror_entries = {"planckbot-fs": fs}
    if "planckbot-synth" in servers:
        mirror_entries["planckbot-synth"] = servers["planckbot-synth"]
    project_changed = _sync_mcp_entries_to_project_scope(
        cfg, new_path, mirror_entries
    )

    if not path_changed and not project_changed:
        return (False, "claude.json already points at that path")

    backup = claude_json.with_suffix(
        f".json.bak-{int(datetime.now().timestamp())}"
    )
    backup.write_text(claude_json.read_text())
    claude_json.write_text(json.dumps(cfg, indent=2))
    return (True, f"updated {claude_json} (backup at {backup.name})")


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
    from planckbot.config import config
    from planckbot.ui.app import start_app

    # Friendly guard: if something is already on the UI port, NiceGUI's own
    # error is cryptic ("address already in use") and users assume the launch
    # failed for a different reason. Probe up-front and tell them directly.
    if _ui_running(config.port):
        print(
            f"PlanckBot UI is already running on http://localhost:{config.port}\n"
            f"  open it in a browser, or stop the running instance with:\n"
            f"    pkill -f 'planckbot$'   # bare-command launches\n"
            f"    pkill -f 'planckbot ui'  # subcommand launches",
            file=sys.stderr,
        )
        return 1
    print(f"PlanckBot UI starting on http://localhost:{config.port} …")
    start_app()
    return 0


def cmd_status(args) -> int:
    """One-shot summary. `--watch N` polls every N seconds until Ctrl-C."""
    watch = getattr(args, "watch", None)
    if watch:
        import time
        try:
            while True:
                # Clear screen between ticks; simple ANSI to avoid curses.
                print("\033[2J\033[H", end="")
                _render_status()
                print(f"\n  (watching — refresh every {watch}s; Ctrl-C to stop)")
                time.sleep(watch)
        except KeyboardInterrupt:
            return 0
    return _render_status()


def _render_status() -> int:
    s = _load_stores()
    config = s["config"]
    triples = s["triples"]
    ckpts = s["checkpoints"]
    cron = s["cron"]
    projects = s["projects"]

    active_project = projects.get_active()
    pid = active_project.id if active_project else None
    total = triples.count_total(project_id=pid)
    counts = triples.count_by_tool(project_id=pid)
    savings = triples.token_savings("proxy:intervene", project_id=pid)
    jobs = cron.list_all(project_id=pid)
    ckpt_rows = ckpts.list_all(project_id=pid)
    all_projects = projects.list_all()

    # Connection: where PlanckBot is observing from, in what mode
    from planckbot.ui.mcp_status import read_mcp_status
    st = read_mcp_status()

    print(f"PlanckBot — db={config.db_path}")
    print("")
    scope_label = (
        f"★ {active_project.name}" if active_project
        else "(no active project — `planckbot project switch <name>`)"
    )
    print(f"  active project     : {scope_label}")
    if all_projects:
        print(
            f"    projects         : {len(all_projects)} total"
            + (
                ", "
                + ", ".join(
                    f"{p.name}{'*' if p.is_active else ''}"
                    for p in all_projects[:5]
                )
                if all_projects else ""
            )
        )
    print("")
    print("  connection         :")
    if st.config_error:
        print(f"    ✗ config error: {st.config_error}")
    elif not st.configured:
        print("    ✗ not connected — run `planckbot init`")
    else:
        fs_health = "live" if st.fs_running else "idle"
        synth_state = (
            "live" if st.synth_healthy
            else "idle" if st.synth_configured else "not configured"
        )
        print(f"    watching        : {st.upstream_path or '(unknown)'}")
        print(f"    proxy mode      : {st.mode or 'observe'}")
        if st.threshold is not None:
            print(f"    threshold       : {st.threshold:.2f}")
        print(f"    planckbot-fs    : {fs_health}  "
              f"({len(st.fs_pids)} proc)")
        print(f"    planckbot-synth : {synth_state}  "
              f"({len(st.synth_pids)} proc)")
    print("")
    print("  workbench UI       :")
    if _ui_running(config.port):
        print(f"    ✓ live on http://localhost:{config.port}")
    else:
        print(f"    ✗ not running — start with `planckbot ui` "
              f"(or bare `planckbot`)")
    print("")
    print(f"  triples total      : {total}")
    print(f"  triples by tool    : "
          + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(none)")
    saved = savings["saved"]
    sign = "+" if saved >= 0 else ""
    pct = (saved / savings["raw_tokens"] * 100) if savings["raw_tokens"] else 0
    print(f"  tokens saved       : {sign}{saved} ({sign}{pct:.0f}%) "
          f"over {savings['intervene_count']} proxy:intervene calls")
    # Cost estimate
    from planckbot.pricing import estimate_cost
    quote = estimate_cost(saved)
    print(f"  estimated $        : {quote.cost_str} at {quote.model_label} "
          f"(${quote.per_million_input_usd:.2f}/M input tokens)")
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


def cmd_ingest_claude_code(args) -> int:
    """Backfill triples from a project's Claude Code JSONL transcripts."""
    from planckbot.config import config
    from planckbot.db.engine import get_connection
    from planckbot.ingest.claude_code import ClaudeCodeJsonlSource
    from planckbot.tools.projects import ProjectStore
    from planckbot.tools.triples import TriplesStore

    conn = get_connection(config.db_path)
    pstore = ProjectStore(conn)

    project_id: str | None = None
    project_path: str | None = args.path

    if args.project:
        proj = pstore.by_name(args.project)
        if proj is None:
            print(f"error: project {args.project!r} not found", file=sys.stderr)
            return 2
        project_id = proj.id
        project_path = project_path or proj.path
    elif not project_path:
        active = pstore.get_active()
        if active is None:
            print(
                "error: no active project; pass --project or --path",
                file=sys.stderr,
            )
            return 2
        project_id = active.id
        project_path = active.path

    src = ClaudeCodeJsonlSource(
        project_path=project_path,
        claude_root=args.claude_root,
        skip_errors=not args.include_errors,
    )
    store = TriplesStore(conn)
    n = src.ingest(
        store, limit=args.limit, since=args.since, project_id=project_id,
    )
    print(
        f"ingested {n} triple(s) from {project_path} "
        f"(project_id={project_id or '(unscoped)'}, limit={args.limit})"
    )
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
    project_id = None
    if args.project:
        project = s["projects"].by_name(args.project)
        if project is None:
            print(f"no such project: {args.project!r}", file=sys.stderr)
            return 1
        project_id = project.id
    job = CronJob(
        name=args.name,
        job_type=args.type,
        params=params,
        interval_seconds=args.interval,
        enabled=0 if args.disabled else 1,
        project_id=project_id,
    )
    s["cron"].add(job)
    scope = f" project={args.project}" if args.project else " (global)"
    print(
        f"added {job.id[:8]} {job.name} "
        f"(every {job.interval_seconds}s){scope}"
    )
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


def cmd_synth_list(_args) -> int:
    s = _load_stores()
    from planckbot.synth.meta import SynthesizedToolStore
    store = SynthesizedToolStore(s["conn"])
    tools = store.list_all()
    if not tools:
        print("no synthesized tools")
        return 0
    print(f"{'ID':<10} {'NAME':<28} {'STATUS':<8} {'CREATED':<20}")
    for t in tools:
        print(f"{t.id[:8]:<10} {t.name[:28]:<28} {t.status:<8} {_fmt_ts(t.created_at)}")
    return 0


def cmd_synth_show(args) -> int:
    s = _load_stores()
    from planckbot.synth.meta import SynthesizedToolStore
    store = SynthesizedToolStore(s["conn"])
    tool = store.by_name(args.name)
    if tool is None:
        print(f"no such tool: {args.name}", file=sys.stderr)
        return 1
    print(f"name:        {tool.name}")
    print(f"status:      {tool.status}")
    print(f"description: {tool.description}")
    print(f"input_schema: {json.dumps(tool.input_schema, indent=2)}")
    print(f"source_file: {tool.source_file_path}")
    print(f"created_by:  {tool.created_by}")
    print(f"gap_report:  {tool.gap_report_id or '-'}")
    print("--- code ---")
    print(tool.code)
    return 0


def cmd_synth_activate(args) -> int:
    s = _load_stores()
    from planckbot.synth.meta import activate_tool
    try:
        tool = activate_tool(args.name, conn=s["conn"])
        print(f"activated {tool.name} ({tool.id[:8]})")
        return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_synth_deactivate(args) -> int:
    s = _load_stores()
    from planckbot.synth.meta import deactivate_tool
    try:
        tool = deactivate_tool(args.name, conn=s["conn"])
        print(f"deactivated {tool.name}")
        return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_synth_create(args) -> int:
    """Register a synthesized tool from a JSON spec file. Spec shape:

        {
          "name": "smart_grep",
          "description": "…",
          "input_schema": {"pattern": "string", "path": "string"},
          "code": "def smart_grep(pattern, path='.'):\n    …"
        }

    Reading code from a file keeps shells from mangling indentation.
    """
    from planckbot.synth.meta import synthesize_tool

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"spec file not found: {args.spec}", file=sys.stderr)
        return 1
    spec = json.loads(spec_path.read_text())
    s = _load_stores()
    try:
        result = synthesize_tool(
            name=spec["name"],
            description=spec.get("description", ""),
            input_schema=spec.get("input_schema", {}),
            code=spec["code"],
            conn=s["conn"],
            gap_report_id=spec.get("gap_report_id"),
            created_by=spec.get("created_by", "cli"),
        )
        print(f"created {result.tool.name} (id={result.tool.id[:8]}, status=draft)")
        print(f"code at {result.source_path}")
        return 0
    except ValueError as e:
        print(f"synthesize failed: {e}", file=sys.stderr)
        return 2


def cmd_synth_author(args) -> int:
    """Generate code for a tool that fuses a gap report's sequence.

    Uses the Anthropic API when ANTHROPIC_API_KEY is set; falls back to a
    stdlib-only template generator otherwise. In both cases the code goes
    through `synthesize_tool`'s AST whitelist before hitting disk.
    """
    from planckbot.synth.authoring import author_tool_for_gap
    from planckbot.synth.detector import GapReportStore
    from planckbot.synth.meta import synthesize_tool

    s = _load_stores()
    gap_store = GapReportStore(s["conn"])
    gap = gap_store.get(args.gap_id)
    if gap is None:
        print(f"gap report not found: {args.gap_id}", file=sys.stderr)
        return 1

    try:
        authored = author_tool_for_gap(
            gap, s["conn"], name=args.name, model=args.model,
            use_api=(None if args.use_api == "auto" else args.use_api == "yes"),
        )
    except Exception as e:
        print(f"authoring failed: {e}", file=sys.stderr)
        return 2

    print(f"[author] source: {authored.source}")
    if authored.usage_tokens:
        print(f"[author] api tokens used: {authored.usage_tokens}")
    print("[author] proposed code:")
    print(authored.code)

    if args.dry_run:
        print("\n[author] dry-run; not persisting")
        return 0

    try:
        result = synthesize_tool(
            name=authored.name,
            description=authored.description,
            input_schema=authored.input_schema,
            code=authored.code,
            conn=s["conn"],
            gap_report_id=gap.id,
            created_by=f"author:{authored.source}",
        )
        print(f"\n[author] stored as synthesized_tools.{result.tool.name}")
        print(f"[author] code at {result.source_path}")
        # Mark the gap report as accepted — it's served its purpose.
        gap_store.set_status(gap.id, "accepted")
        return 0
    except ValueError as e:
        print(f"\n[author] AST gate rejected the generated code: {e}",
              file=sys.stderr)
        return 3


def cmd_synth_gaps(_args) -> int:
    s = _load_stores()
    from planckbot.synth.detector import GapReportStore
    store = GapReportStore(s["conn"])
    reports = store.list_all(limit=20)
    if not reports:
        print("no gap reports yet")
        return 0
    print(f"{'ID':<10} {'OCC':>4} {'STATUS':<10} SEQUENCE")
    for r in reports:
        seq = " → ".join(r.tool_sequence)
        print(f"{r.id[:8]:<10} {r.occurrences:>4} {r.status:<10} {seq}")
    return 0


# --- project subcommands ---------------------------------------------------


def cmd_project_list(_args) -> int:
    s = _load_stores()
    projects = s["projects"].list_all()
    if not projects:
        print("no projects yet — create one with `planckbot project create`")
        return 0
    print(f"{'':<2} {'NAME':<24} {'ID':<10} {'PATH':<50} {'CREATED':<20}")
    for p in projects:
        marker = "★" if p.is_active else " "
        print(
            f"{marker:<2} {p.name[:24]:<24} {p.id[:8]:<10} "
            f"{p.path[:50]:<50} {_fmt_ts(p.created_at):<20}"
        )
    return 0


def cmd_project_show(args) -> int:
    s = _load_stores()
    target = args.name
    project = (
        s["projects"].by_name(target)
        if target
        else s["projects"].get_active()
    )
    if project is None:
        print(
            f"no such project: {target!r}" if target
            else "no active project — use `planckbot project switch <name>`",
            file=sys.stderr,
        )
        return 1
    # Quick stats
    triples = s["triples"].count_total(project_id=project.id)
    ckpts = s["checkpoints"].count(project_id=project.id)

    print(f"name        : {project.name}")
    print(f"id          : {project.id}")
    print(f"path        : {project.path}")
    print(f"description : {project.description or '-'}")
    print(f"active      : {'yes' if project.is_active else 'no'}")
    print(f"created_at  : {_fmt_ts(project.created_at)}")
    print(f"last_used_at: {_fmt_ts(project.last_used_at)}")
    print(f"triples     : {triples}")
    print(f"checkpoints : {ckpts}")
    return 0


def cmd_project_create(args) -> int:
    s = _load_stores()
    path = Path(args.path).expanduser().resolve()
    try:
        project = s["projects"].create(
            name=args.name,
            path=str(path),
            description=args.description,
            activate=args.activate,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"created project {project.name} ({project.id[:8]}) → {project.path}")

    if args.adopt_legacy:
        touched = s["projects"].adopt_legacy(project.id)
        total = sum(touched.values())
        print(f"  adopted {total} pre-v6 row(s): {touched}")

    if args.activate:
        ok, msg = _rewrite_mcp_upstream_path(str(path))
        print(f"  activated — {msg}")
        if ok:
            print(
                "  restart Claude Code so it picks up the new folder."
            )
    return 0


def cmd_project_switch(args) -> int:
    s = _load_stores()
    project = s["projects"].by_name(args.name)
    if project is None:
        print(f"no such project: {args.name!r}", file=sys.stderr)
        return 1
    s["projects"].set_active(project.id)
    print(f"switched active project → {project.name} ({project.path})")
    ok, msg = _rewrite_mcp_upstream_path(project.path)
    print(f"  {msg}")
    if ok:
        print("  restart Claude Code so it picks up the new folder.")
    return 0


def cmd_project_delete(args) -> int:
    s = _load_stores()
    project = s["projects"].by_name(args.name)
    if project is None:
        print(f"no such project: {args.name!r}", file=sys.stderr)
        return 1
    if project.is_active and not args.yes:
        print(
            f"{project.name} is the active project. "
            "Switch to a different one first, or pass --yes to force.",
            file=sys.stderr,
        )
        return 2
    if args.purge and not args.yes:
        resp = input(
            f"really delete {project.name} AND all its triples/checkpoints/"
            f"cron/synth/gaps/experiments? [y/N] "
        )
        if resp.strip().lower() != "y":
            print("aborted")
            return 0
    s["projects"].delete(project.id, cascade=args.purge)
    note = "+ scoped rows" if args.purge else "(scoped rows kept — re-adopt via another project)"
    print(f"deleted {project.name} {note}")
    return 0


def cmd_project_rename(args) -> int:
    s = _load_stores()
    project = s["projects"].by_name(args.old)
    if project is None:
        print(f"no such project: {args.old!r}", file=sys.stderr)
        return 1
    try:
        s["projects"].rename(project.id, args.new)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"renamed {args.old} → {args.new}")
    return 0


def cmd_project_route(args) -> int:
    """Set (or inspect) a project's MCP-preference routing mode."""
    from planckbot.tools import routing

    s = _load_stores()
    project = s["projects"].by_name(args.name)
    if project is None:
        print(f"no such project: {args.name!r}", file=sys.stderr)
        return 1
    project_path = Path(project.path)
    if args.mode is None:
        current = routing.get_mode(project_path)
        print(f"{project.name}: {current}  ({project_path})")
        return 0
    try:
        state = routing.set_mode(project_path, args.mode)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{project.name}: → {state.mode}")
    if state.deny_entries:
        print(f"  injected deny: {state.deny_entries}")
    if state.mode != "off":
        print("  restart Claude Code in that folder for changes to take effect.")
    return 0


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


# --- init + systemd --------------------------------------------------------


def cmd_init(args) -> int:
    """First-run setup: create the data dir, apply migrations, (optionally)
    register the MCP servers with Claude Code.

    We never overwrite an existing entry in ~/.claude.json unless --force is
    passed. Without --force we show a diff and ask the user to add it by
    hand. This protects any custom config the user already has.
    """
    from planckbot.config import config
    from planckbot.db.engine import get_connection

    print(f"[init] data dir: {config.data_dir}")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    (config.data_dir / "checkpoints").mkdir(exist_ok=True)
    (config.data_dir / "exports").mkdir(exist_ok=True)
    (config.data_dir / "fixtures").mkdir(exist_ok=True)

    # Migrations run in get_connection()
    conn = get_connection(config.db_path)
    conn.execute("SELECT MAX(version) FROM schema_version")
    print(f"[init] db ready: {config.db_path}")

    # Locate the two console_script binaries. In an editable venv install the
    # binaries live next to sys.executable; in a global install, on $PATH.
    from shutil import which
    exec_dir = Path(sys.executable).parent
    def _find_bin(name: str) -> str | None:
        candidate = exec_dir / name
        if candidate.exists():
            return str(candidate)
        return which(name)
    fs_bin = _find_bin("planckbot-mcp")
    synth_bin = _find_bin("planckbot-synth")
    if not fs_bin or not synth_bin:
        print(
            "[init] warning: could not locate planckbot-mcp / planckbot-synth "
            "on $PATH. Install the package (`pip install planckbot` or "
            "`uv pip install -e .`) before calling `planckbot init`.",
            file=sys.stderr,
        )

    entries = {}
    upstream: str | None = None
    if fs_bin:
        upstream = args.upstream_path or os.environ.get(
            "PLANCKBOT_UPSTREAM_PATH",
            str(Path.cwd()),
        )
        upstream = str(Path(upstream).expanduser().resolve())
        npx = args.npx or which("npx") or "/usr/bin/npx"
        entries["planckbot-fs"] = {
            "command": fs_bin,
            "args": [
                "--mode", args.mode,
                "--name", "planckbot-fs",
                "--",
                npx, "-y", "@modelcontextprotocol/server-filesystem",
                upstream,
            ],
        }
    if synth_bin:
        entries["planckbot-synth"] = {"command": synth_bin}

    # v6: auto-register the upstream path as a project so per-project
    # scoping has a row to tag against from the very first tool call.
    # Re-running `init` with the same path is idempotent: we find the
    # existing row by path and just re-activate it.
    if upstream and not args.print_config:
        from planckbot.tools.projects import ProjectStore
        pstore = ProjectStore(conn)
        project = pstore.by_path(upstream)
        if project is None:
            name = args.project_name or _derive_project_name(
                upstream, pstore
            )
            project = pstore.create(
                name=name, path=upstream, activate=True,
            )
            print(f"[init] created project {project.name} → {upstream}")
            # On a first install the pre-v6 DB is empty; on an upgrade
            # there may be legacy triples/checkpoints. Only adopt legacy
            # data when the user passes --adopt-legacy to avoid mixing a
            # prior watched folder into the new project by accident.
            if args.adopt_legacy:
                touched = pstore.adopt_legacy(project.id)
                total = sum(touched.values())
                if total:
                    print(f"[init]   adopted {total} pre-v6 row(s): {touched}")
        else:
            pstore.set_active(project.id)
            print(
                f"[init] reused existing project {project.name} "
                f"({project.id[:8]}) → {upstream}"
            )

    claude_json = Path.home() / ".claude.json"

    if args.print_config:
        # Just show the JSON block and exit.
        print(json.dumps({"mcpServers": entries}, indent=2))
        return 0

    if not claude_json.exists():
        if args.claude_config or input(
            f"[init] write {claude_json} with PlanckBot MCP entries? [y/N] "
        ).lower() == "y":
            cfg = {"mcpServers": dict(entries)}
            if upstream:
                _sync_mcp_entries_to_project_scope(cfg, upstream, entries)
            claude_json.write_text(json.dumps(cfg, indent=2))
            print(f"[init] wrote {claude_json}")
        else:
            print("[init] skipped writing claude.json — here's the block:")
            print(json.dumps({"mcpServers": entries}, indent=2))
        _print_next_steps()
        return 0

    # Existing config: parse, detect conflicts, merge only when safe.
    try:
        cfg = json.loads(claude_json.read_text())
    except json.JSONDecodeError as e:
        print(f"[init] ~/.claude.json is not valid JSON: {e}", file=sys.stderr)
        print("[init] fix it first, then re-run `planckbot init`")
        return 2

    existing = cfg.setdefault("mcpServers", {})
    conflicts = [name for name in entries if name in existing]
    added = [name for name in entries if name not in existing]

    if conflicts and not args.force:
        print(
            f"[init] already present in ~/.claude.json: {conflicts}\n"
            "[init] re-run with --force to overwrite, "
            "or add the block manually:"
        )
        print(json.dumps({"mcpServers": entries}, indent=2))
        _print_next_steps()
        return 0

    for name, spec in entries.items():
        existing[name] = spec
    # Claude Code writes `mcpServers: {}` into every project section on
    # first open, and that empty dict shadows the global scope. Mirror
    # our entries into the target project's scope so they actually show
    # up in any existing Claude Code session for this folder.
    project_synced = False
    if upstream:
        project_synced = _sync_mcp_entries_to_project_scope(
            cfg, upstream, entries
        )
    # Backup before writing
    backup = claude_json.with_suffix(
        f".json.bak-{int(datetime.now().timestamp())}"
    )
    backup.write_text(claude_json.read_text())
    claude_json.write_text(json.dumps(cfg, indent=2))
    print(f"[init] updated {claude_json} (backup at {backup.name})")
    print(f"[init]   added: {added or '(none new)'}")
    if conflicts:
        print(f"[init]   overwrote: {conflicts}")
    if project_synced:
        print(f"[init]   mirrored into project scope: {upstream}")

    _print_next_steps()
    return 0


def _derive_project_name(path: str, store) -> str:
    """Pick a unique project name based on the folder basename.

    `planckbot` → `planckbot`, and if that's taken, `planckbot-2`, etc.
    Falls back to a UUID prefix when the basename is empty (e.g. `/`).
    """
    base = Path(path).name or "project"
    # Normalize: strip anything that'd make it awkward on the CLI.
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in base)
    safe = safe.strip("-") or "project"
    if store.by_name(safe) is None:
        return safe
    for i in range(2, 100):
        candidate = f"{safe}-{i}"
        if store.by_name(candidate) is None:
            return candidate
    import uuid
    return f"{safe}-{uuid.uuid4().hex[:6]}"


def _print_next_steps() -> None:
    print("\n[init] next steps:")
    print("  1. Restart Claude Code so it picks up the new MCP servers.")
    print("  2. Launch the workbench:        planckbot ui")
    print("  3. Start the background loop:   planckbot systemd install")
    print("                                  # or ad-hoc: planckbot cron daemon")
    print("  4. Read the docs:               planckbot-ui /how-it-works + /paper")


def cmd_systemd_install(_args) -> int:
    """Write a systemd --user unit for the cron daemon and enable it.
    Linux-only. Uses `systemctl --user` with the current user's context."""
    import shutil
    import subprocess

    if shutil.which("systemctl") is None:
        print("[systemd] systemctl not found; skipping. "
              "Start the daemon manually with `planckbot cron daemon`.",
              file=sys.stderr)
        return 2

    planckbot_bin = shutil.which("planckbot")
    if planckbot_bin is None:
        print("[systemd] planckbot not on $PATH. Install the package first.",
              file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[2]

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_file = unit_dir / "planckbot-cron.service"

    unit_body = f"""[Unit]
Description=PlanckBot cron daemon — scheduled jobs (autolabel, detect_tool_gaps, ...)
Documentation=https://github.com/opcastil11/planckbot
After=default.target

[Service]
Type=simple
ExecStart={planckbot_bin} cron daemon
WorkingDirectory={repo_root}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

    if unit_file.exists():
        print(f"[systemd] {unit_file} already exists — re-writing")
    unit_file.write_text(unit_body)
    print(f"[systemd] wrote {unit_file}")

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "planckbot-cron.service"],
    ):
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[systemd] `{' '.join(cmd)}` failed:\n{res.stderr}",
                  file=sys.stderr)
            return res.returncode

    print("[systemd] service enabled + started. Check status with:")
    print("           systemctl --user status planckbot-cron.service")
    print("[systemd] to survive logout, also run: sudo loginctl "
          "enable-linger $USER")
    return 0


def cmd_doctor(args) -> int:
    """Run every health check and print a report. Exit code = worst status."""
    from planckbot.doctor import (
        CHECK_FAIL, CHECK_OK, CHECK_WARN, run_checks, worst,
    )

    results = run_checks()

    if getattr(args, "json", False):
        import json as _json
        out = {
            "checks": [
                {"title": r.title, "status": r.status,
                 "detail": r.detail, "fix": r.fix}
                for r in results
            ],
            "summary": {
                "ok":   sum(1 for r in results if r.status == CHECK_OK),
                "warn": sum(1 for r in results if r.status == CHECK_WARN),
                "fail": sum(1 for r in results if r.status == CHECK_FAIL),
                "worst": worst(results),
            },
        }
        print(_json.dumps(out, indent=2))
        w = out["summary"]["worst"]
    else:
        ICON = {
            CHECK_OK: "\033[32m✓\033[0m",
            CHECK_WARN: "\033[33m⚠\033[0m",
            CHECK_FAIL: "\033[31m✗\033[0m",
        }
        print("PlanckBot — health check\n")
        for r in results:
            print(f"  {ICON[r.status]} {r.title}")
            print(f"      {r.detail}")
            if r.fix:
                print(f"      → \033[36m{r.fix}\033[0m")
        print()
        ok = sum(1 for r in results if r.status == CHECK_OK)
        warn = sum(1 for r in results if r.status == CHECK_WARN)
        fail = sum(1 for r in results if r.status == CHECK_FAIL)
        print(f"  {ok} ok, {warn} warn, {fail} fail")
        w = worst(results)

    if w == CHECK_FAIL:
        return 2
    if w == CHECK_WARN:
        return 1
    return 0


def cmd_demo(args) -> int:
    """Populate the DB with synthetic triples so the dashboard has data
    to show on a fresh install. `planckbot demo clear` removes them."""
    from planckbot.config import config
    from planckbot.db.engine import get_connection
    from planckbot.db.models import Triple

    conn = get_connection(config.db_path)
    action = args.action

    if action == "clear":
        n = conn.execute(
            "DELETE FROM triples WHERE source = 'demo' RETURNING id"
        ).fetchall()
        conn.commit()
        # Also clear demo checkpoints + gap_reports
        conn.execute(
            "DELETE FROM model_checkpoints WHERE name LIKE 'demo_%'"
        )
        conn.execute(
            "DELETE FROM gap_reports WHERE proposed_name LIKE 'demo_%'"
        )
        conn.commit()
        print(f"[demo] cleared {len(n)} demo triple(s) + checkpoints + gaps")
        return 0

    if action != "load":
        print("usage: planckbot demo {load|clear}", file=sys.stderr)
        return 2

    # Generate synthetic triples that mimic a real session.
    import hashlib
    from datetime import datetime, timedelta, timezone

    demo_data = [
        ("list_directory", {"path": "/work/repo"},
         "[DIR] .git\n[DIR] .venv\n[DIR] node_modules\n[DIR] src\n"
         "[DIR] tests\n[DIR] docs\n[FILE] package.json\n"
         "[FILE] pyproject.toml\n[FILE] README.md",
         "[DIR] src\n[DIR] tests\n[FILE] package.json"),
        ("list_directory", {"path": "/work/api"},
         "[DIR] .git\n[DIR] __pycache__\n[DIR] src\n[DIR] tests\n"
         "[FILE] Dockerfile\n[FILE] pyproject.toml",
         "[DIR] src\n[FILE] Dockerfile"),
        ("read_text_file", {"path": "/work/repo/package.json"},
         '{\n  "name": "demo-app",\n  "version": "1.0.0",\n  '
         '"dependencies": {\n    "react": "^18.0",\n    "next": "^14"\n  '
         '},\n  "scripts": {...},\n  "devDependencies": {...}\n}',
         '"name": "demo-app"\n"react": "^18.0"\n"next": "^14"'),
        ("search_files", {"pattern": "*.py", "path": "/work/repo/src"},
         "src/main.py\nsrc/app/routes.py\nsrc/app/models.py\n"
         "src/utils/helpers.py\nsrc/tests/test_main.py",
         "src/main.py\nsrc/app/routes.py\nsrc/app/models.py"),
        ("list_directory", {"path": "/work/web"},
         "[DIR] .next\n[DIR] node_modules\n[DIR] public\n[DIR] src\n"
         "[FILE] next.config.js\n[FILE] package.json",
         "[DIR] public\n[DIR] src\n[FILE] next.config.js"),
    ]

    inserted = 0
    now = datetime.now(timezone.utc)
    for i, (tool, inp, out, filt) in enumerate(demo_data):
        ts = (now - timedelta(minutes=60 - i * 10)).isoformat()
        t = Triple(
            id=f"demo-{hashlib.md5((tool + str(inp)).encode()).hexdigest()[:10]}-{i}",
            tool_name=tool,
            input_data=json.dumps(inp),
            output_data=out,
            input_tokens=len(json.dumps(inp)) // 4,
            output_tokens=len(out) // 4,
            filtered_output=filt,
            filtered_tokens=len(filt) // 4,
            source="demo",
            created_at=ts,
        )
        row = t.to_row()
        placeholders = ", ".join("?" for _ in row)
        cols = ", ".join(row.keys())
        try:
            conn.execute(
                f"INSERT INTO triples ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            inserted += 1
        except Exception as e:
            # Likely already present from a previous `demo load` — skip quietly.
            if "UNIQUE" not in str(e):
                raise
    conn.commit()

    print(f"[demo] inserted {inserted} synthetic triple(s)")
    print("[demo] open the dashboard: planckbot ui")
    print("[demo] to undo: planckbot demo clear")
    return 0


def cmd_bless(args) -> int:
    """Mark a checkpoint safe to serve. Requires explicit operator action
    because the proxy refuses to serve un-blessed adapters in intervene
    mode (a guard against accidentally activating a regressive adapter).
    """
    s = _load_stores()
    ckpt = s["checkpoints"].get(args.ckpt_id)
    if ckpt is None:
        print(f"checkpoint not found: {args.ckpt_id}", file=sys.stderr)
        return 1
    try:
        s["checkpoints"].bless(
            ckpt.id, tuned_threshold=args.threshold,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"blessed {ckpt.name} ({ckpt.id[:8]})")
    if args.threshold is not None:
        print(f"  tuned_threshold = {args.threshold}")
    if args.activate:
        s["checkpoints"].activate(ckpt.id)
        print(f"  activated — this is now the live adapter for {ckpt.tool_name}")
    return 0


def cmd_unbless(args) -> int:
    """Revoke the blessed flag AND deactivate the checkpoint. Use when an
    adapter is found to regress."""
    s = _load_stores()
    ckpt = s["checkpoints"].get(args.ckpt_id)
    if ckpt is None:
        print(f"checkpoint not found: {args.ckpt_id}", file=sys.stderr)
        return 1
    s["checkpoints"].unbless(ckpt.id)
    print(f"unblessed + deactivated {ckpt.name}")
    return 0


def cmd_uninstall(args) -> int:
    """Remove PlanckBot from the system. The Python package itself (pip)
    is NOT uninstalled — this removes PlanckBot's runtime state:
    ~/.claude.json entries, systemd user unit, and optionally the data
    directory. Always prompts before removing unless --yes is passed."""
    import shutil
    import subprocess
    from planckbot.config import config

    def _confirm(msg: str) -> bool:
        if args.yes:
            return True
        return input(f"{msg} [y/N] ").strip().lower() == "y"

    claude_json = Path.home() / ".claude.json"
    unit_file = Path.home() / ".config/systemd/user/planckbot-cron.service"

    print("PlanckBot uninstall — what will be removed:")
    if claude_json.exists():
        print(f"  ~/.claude.json MCP entries (planckbot-fs, planckbot-synth)")
    if unit_file.exists():
        print(f"  systemd user unit: {unit_file}")
    if config.data_dir.exists() and args.purge_data:
        print(f"  data dir: {config.data_dir} ({_dir_size_mb(config.data_dir):.1f} MB)")
    print()
    if not _confirm("Proceed?"):
        print("aborted")
        return 0

    # 0. Deactivate any active project so a fresh re-install starts clean.
    # We do NOT delete the project rows or their triples/checkpoints —
    # that's what --purge-data is for. A user who uninstalls and later
    # reinstalls against the same folder gets their history back.
    try:
        from planckbot.db.engine import get_connection
        from planckbot.tools.projects import ProjectStore
        conn = get_connection(config.db_path)
        ProjectStore(conn).deactivate_all()
    except Exception as e:
        print(f"[uninstall] note: could not deactivate projects: {e}",
              file=sys.stderr)

    # 1. Remove MCP entries from claude.json — both global scope and any
    #    project scope where init/switch mirrored them.
    if claude_json.exists():
        try:
            cfg = json.loads(claude_json.read_text())
            servers = cfg.get("mcpServers", {}) or {}
            removed = []
            for name in ("planckbot-fs", "planckbot-synth"):
                if name in servers:
                    del servers[name]
                    removed.append(name)
            project_scopes_cleaned = []
            for path, proj in (cfg.get("projects") or {}).items():
                proj_mcp = proj.get("mcpServers") or {}
                dropped = False
                for name in ("planckbot-fs", "planckbot-synth"):
                    if name in proj_mcp:
                        del proj_mcp[name]
                        dropped = True
                if dropped:
                    project_scopes_cleaned.append(path)
            if removed or project_scopes_cleaned:
                backup = claude_json.with_suffix(
                    f".json.bak-{int(datetime.now().timestamp())}"
                )
                backup.write_text(claude_json.read_text())
                claude_json.write_text(json.dumps(cfg, indent=2))
                if removed:
                    print(f"[uninstall] removed from claude.json: {removed} "
                          f"(backup at {backup.name})")
                if project_scopes_cleaned:
                    print(f"[uninstall] cleaned project scopes: "
                          f"{project_scopes_cleaned}")
        except json.JSONDecodeError:
            print("[uninstall] claude.json is malformed — leaving alone")

    # 2. systemd unit
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "disable", "--now",
             "planckbot-cron.service"],
            capture_output=True,
        )
    if unit_file.exists():
        unit_file.unlink()
        print(f"[uninstall] removed {unit_file}")
        if shutil.which("systemctl"):
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
            )

    # 3. Data dir (destructive — only with --purge-data)
    if args.purge_data and config.data_dir.exists():
        if _confirm(f"REALLY delete {config.data_dir}? This is irreversible."):
            shutil.rmtree(config.data_dir)
            print(f"[uninstall] removed {config.data_dir}")

    # 4. Tell user how to finish
    print()
    print("[uninstall] done. To finish removing PlanckBot:")
    print("  pip uninstall planckbot")
    print("  rm -rf .venv   # if you want to drop the venv entirely")
    return 0


def _dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def cmd_systemd_uninstall(_args) -> int:
    import shutil
    import subprocess

    if shutil.which("systemctl") is None:
        print("[systemd] systemctl not found; nothing to do.", file=sys.stderr)
        return 0

    unit_file = Path.home() / ".config/systemd/user/planckbot-cron.service"
    for cmd in (
        ["systemctl", "--user", "disable", "--now", "planckbot-cron.service"],
        ["systemctl", "--user", "daemon-reload"],
    ):
        subprocess.run(cmd, capture_output=True, text=True)
    if unit_file.exists():
        unit_file.unlink()
        print(f"[systemd] removed {unit_file}")
    print("[systemd] service disabled and removed.")
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
    sp.add_argument(
        "--watch", type=float, default=None, metavar="SECONDS",
        help="Refresh every N seconds until Ctrl-C (a terminal dashboard).",
    )
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

    # ingest <subsub>
    ingest_p = sub.add_parser(
        "ingest",
        help="Pull triples from external sources (e.g. Claude Code JSONL).",
    )
    ingest_sub = ingest_p.add_subparsers(dest="ingest_command")

    ip = ingest_sub.add_parser(
        "claude-code",
        help="Backfill triples from a project's Claude Code JSONL transcripts. "
             "Idempotent — safe to re-run.",
    )
    ip.add_argument(
        "--project",
        help="Project name. Defaults to the active project.",
    )
    ip.add_argument(
        "--path",
        help="Override project path (used to derive Claude Code slug). "
             "If both --project and --path are given, --path wins.",
    )
    ip.add_argument(
        "--limit", type=int, default=5000,
        help="Max number of new triples per run (default 5000).",
    )
    ip.add_argument(
        "--since",
        help="ISO timestamp; only ingest tool_use entries newer than this.",
    )
    ip.add_argument(
        "--claude-root",
        help="Override ~/.claude/projects (for tests / unusual setups).",
    )
    ip.add_argument(
        "--include-errors", action="store_true",
        help="Keep tool calls whose result is_error=True (default: skip).",
    )
    ip.set_defaults(func=cmd_ingest_claude_code)

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
    cp.add_argument(
        "--project",
        help="Name of the project this job should run under "
             "(default: global).",
    )
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

    # synth <subsub>
    synth_p = sub.add_parser("synth", help="Manage Layer-D synthesized tools.")
    synth_sub = synth_p.add_subparsers(dest="synth_command")

    sp2 = synth_sub.add_parser("list", help="List all synthesized tools.")
    sp2.set_defaults(func=cmd_synth_list)

    sp2 = synth_sub.add_parser("show", help="Show one tool's code + metadata.")
    sp2.add_argument("name")
    sp2.set_defaults(func=cmd_synth_show)

    sp2 = synth_sub.add_parser(
        "activate", help="Mark a tool active; SIGHUP the synth MCP server.",
    )
    sp2.add_argument("name")
    sp2.set_defaults(func=cmd_synth_activate)

    sp2 = synth_sub.add_parser(
        "deactivate", help="Retire a tool; SIGHUP the synth MCP server.",
    )
    sp2.add_argument("name")
    sp2.set_defaults(func=cmd_synth_deactivate)

    sp2 = synth_sub.add_parser(
        "create", help="Register a new synthesized tool from a JSON spec.",
    )
    sp2.add_argument("spec", help="Path to JSON spec file.")
    sp2.set_defaults(func=cmd_synth_create)

    sp2 = synth_sub.add_parser("gaps", help="List detected tool-gap reports.")
    sp2.set_defaults(func=cmd_synth_gaps)

    sp2 = synth_sub.add_parser(
        "author",
        help="Generate code for a merged tool from a gap report "
             "(uses Claude API if available, template otherwise).",
    )
    sp2.add_argument("gap_id", help="Gap report UUID or 8-char prefix.")
    sp2.add_argument("--name", help="Override the proposed tool name.")
    sp2.add_argument("--model", default="claude-sonnet-4-6",
                     help="Anthropic model id (default: claude-sonnet-4-6).")
    sp2.add_argument("--use-api", choices=["auto", "yes", "no"], default="auto",
                     help="Force API usage on/off (default: auto — API "
                          "when ANTHROPIC_API_KEY is set).")
    sp2.add_argument("--dry-run", action="store_true",
                     help="Print the generated code but don't persist it.")
    sp2.set_defaults(func=cmd_synth_author)

    # project <subsub>
    project_p = sub.add_parser(
        "project",
        help="Manage target-folder projects (the 'which folder should "
             "PlanckBots watch' question).",
    )
    project_sub = project_p.add_subparsers(dest="project_command")

    pp = project_sub.add_parser("list", help="List all projects.")
    pp.set_defaults(func=cmd_project_list)

    pp = project_sub.add_parser(
        "show",
        help="Show a project's config + stats. Omit name for the active one.",
    )
    pp.add_argument("name", nargs="?")
    pp.set_defaults(func=cmd_project_show)

    pp = project_sub.add_parser(
        "create", help="Register a new target folder as a project.",
    )
    pp.add_argument("name", help="Short unique name (no spaces).")
    pp.add_argument(
        "--path", required=True,
        help="Absolute path to the folder PlanckBots should watch.",
    )
    pp.add_argument("--description", default=None)
    pp.add_argument(
        "--activate", action="store_true",
        help="Make this the active project; rewrites ~/.claude.json.",
    )
    pp.add_argument(
        "--adopt-legacy", action="store_true",
        help="Reassign every pre-v6 row (NULL project_id) to this project.",
    )
    pp.set_defaults(func=cmd_project_create)

    pp = project_sub.add_parser(
        "switch",
        help="Set the active project (updates DB + ~/.claude.json).",
    )
    pp.add_argument("name")
    pp.set_defaults(func=cmd_project_switch)

    pp = project_sub.add_parser("delete", help="Delete a project.")
    pp.add_argument("name")
    pp.add_argument(
        "--purge", action="store_true",
        help="Also delete triples/checkpoints/etc. for this project.",
    )
    pp.add_argument("--yes", "-y", action="store_true")
    pp.set_defaults(func=cmd_project_delete)

    pp = project_sub.add_parser("rename", help="Rename a project.")
    pp.add_argument("old")
    pp.add_argument("new")
    pp.set_defaults(func=cmd_project_rename)

    pp = project_sub.add_parser(
        "route",
        help="Show or set a project's MCP-routing mode "
             "(off | soft | hard). Soft nudges Claude via CLAUDE.md; "
             "hard also denies native Read/Glob/Grep in settings.json.",
    )
    pp.add_argument("name")
    pp.add_argument(
        "mode", nargs="?", choices=["off", "soft", "hard"],
        help="Target mode. Omit to show current mode.",
    )
    pp.set_defaults(func=cmd_project_route)

    # init (first-run setup)
    sp = sub.add_parser(
        "init",
        help="First-run setup: create data dir, migrate DB, "
             "register MCP servers with Claude Code.",
    )
    sp.add_argument(
        "--mode", choices=["observe", "suggest", "intervene"],
        default="observe", help="Proxy mode for planckbot-fs (default: observe).",
    )
    sp.add_argument(
        "--upstream-path",
        help="Absolute path served by the filesystem MCP upstream. "
             "Defaults to $PLANCKBOT_UPSTREAM_PATH or the current directory.",
    )
    sp.add_argument("--npx", help="Path to npx (autodetected if omitted).")
    sp.add_argument(
        "--claude-config", action="store_true",
        help="Write ~/.claude.json without prompting.",
    )
    sp.add_argument(
        "--force", action="store_true",
        help="Overwrite existing MCP server entries in ~/.claude.json.",
    )
    sp.add_argument(
        "--print-config", action="store_true",
        help="Print the JSON block and exit without touching any file.",
    )
    sp.add_argument(
        "--project-name",
        help="Name for the auto-created project (default: folder basename).",
    )
    sp.add_argument(
        "--adopt-legacy", action="store_true",
        help="Reassign pre-v6 rows (NULL project_id) to the new project.",
    )
    sp.set_defaults(func=cmd_init)

    # doctor
    sp = sub.add_parser(
        "doctor",
        help="Run health checks on this install and print a report.",
    )
    sp.add_argument("--json", action="store_true",
                    help="Output structured JSON instead of pretty text.")
    sp.set_defaults(func=cmd_doctor)

    # bless + unbless
    sp = sub.add_parser(
        "bless",
        help="Mark a trained checkpoint safe to serve in intervene mode.",
    )
    sp.add_argument("ckpt_id", help="Checkpoint UUID (or 8-char prefix).")
    sp.add_argument("--threshold", type=float,
                    help="Per-checkpoint confidence threshold (0.0-1.0). "
                         "Overrides the proxy's global default.")
    sp.add_argument("--activate", action="store_true",
                    help="Also activate this checkpoint for its tool.")
    sp.set_defaults(func=cmd_bless)

    sp = sub.add_parser(
        "unbless",
        help="Revoke `blessed` and deactivate — use after finding regression.",
    )
    sp.add_argument("ckpt_id")
    sp.set_defaults(func=cmd_unbless)

    # demo
    sp = sub.add_parser(
        "demo",
        help="Populate the DB with synthetic data to preview the dashboard "
             "(reversible with `demo clear`).",
    )
    sp.add_argument("action", choices=["load", "clear"],
                    help="'load' inserts synthetic triples; "
                         "'clear' removes rows tagged source=demo.")
    sp.set_defaults(func=cmd_demo)

    # uninstall
    sp = sub.add_parser(
        "uninstall",
        help="Remove MCP entries from ~/.claude.json + systemd unit. "
             "Does NOT touch the pip-installed package.",
    )
    sp.add_argument("--yes", "-y", action="store_true",
                    help="Skip confirmation prompts.")
    sp.add_argument("--purge-data", action="store_true",
                    help="Also delete the data/ directory (DB, adapters, "
                         "everything). Irreversible.")
    sp.set_defaults(func=cmd_uninstall)

    # systemd
    sy = sub.add_parser(
        "systemd",
        help="Install/uninstall the cron daemon as a systemd user unit "
             "(Linux only).",
    )
    sy_sub = sy.add_subparsers(dest="systemd_command")
    sy_i = sy_sub.add_parser(
        "install",
        help="Write ~/.config/systemd/user/planckbot-cron.service and start it.",
    )
    sy_i.set_defaults(func=cmd_systemd_install)
    sy_u = sy_sub.add_parser(
        "uninstall",
        help="Stop + remove the planckbot-cron systemd user unit.",
    )
    sy_u.set_defaults(func=cmd_systemd_uninstall)

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

    if args.command == "synth" and getattr(args, "synth_command", None) is None:
        parser.parse_args(["synth", "--help"])
        return 2

    if args.command == "project" and getattr(args, "project_command", None) is None:
        parser.parse_args(["project", "--help"])
        return 2

    if args.command == "systemd" and getattr(args, "systemd_command", None) is None:
        parser.parse_args(["systemd", "--help"])
        return 2

    if args.command == "ingest" and getattr(args, "ingest_command", None) is None:
        parser.parse_args(["ingest", "--help"])
        return 2

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
