"""`planckbot doctor` — a health check that runs 10+ diagnostics against a
local install and prints a pass/fail report with suggested fixes.

The goal is that a dev new to PlanckBot can run this once after `planckbot
init`, see every check green, and know the system is actually wired up.
When something is wrong, the output points at the specific remedy rather
than making them grep the README.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CHECK_OK = "ok"
CHECK_WARN = "warn"
CHECK_FAIL = "fail"


@dataclass
class CheckResult:
    status: str       # ok | warn | fail
    title: str
    detail: str
    fix: str | None = None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        return CheckResult(
            status=CHECK_FAIL,
            title="Python version",
            detail=f"PlanckBot requires 3.10+, this venv runs {sys.version.split()[0]}.",
            fix="Recreate the venv with a newer Python: `uv venv --python 3.12 .venv`.",
        )
    return CheckResult(
        status=CHECK_OK,
        title="Python version",
        detail=f"{major}.{minor}.{sys.version_info[2]}",
    )


def _check_in_venv() -> CheckResult:
    in_venv = (
        hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    )
    if not in_venv:
        return CheckResult(
            status=CHECK_WARN,
            title="Virtualenv",
            detail="Not running from a virtualenv — that's fine for system-wide "
                   "installs but the common pattern is a .venv/ per project.",
            fix="Activate with `source .venv/bin/activate` or use "
                "`uv venv .venv && uv pip install -e '.[dev]'`.",
        )
    return CheckResult(
        status=CHECK_OK,
        title="Virtualenv",
        detail=f"active at {sys.prefix}",
    )


def _check_package_installed() -> CheckResult:
    try:
        import planckbot  # noqa: F401
        from planckbot import __version__
        return CheckResult(
            status=CHECK_OK,
            title="PlanckBot package",
            detail=f"import ok — v{__version__}",
        )
    except Exception as e:
        return CheckResult(
            status=CHECK_FAIL,
            title="PlanckBot package",
            detail=f"cannot import: {e}",
            fix="Install with `uv pip install -e '.[dev]'` from the repo root.",
        )


def _check_binaries() -> CheckResult:
    exec_dir = Path(sys.executable).parent
    missing = []
    for name in ("planckbot", "planckbot-mcp", "planckbot-synth"):
        if not (exec_dir / name).exists() and not shutil.which(name):
            missing.append(name)
    if missing:
        return CheckResult(
            status=CHECK_FAIL,
            title="Console scripts",
            detail=f"missing: {missing}",
            fix="Re-install: `uv pip install -e .`",
        )
    return CheckResult(
        status=CHECK_OK,
        title="Console scripts",
        detail="planckbot, planckbot-mcp, planckbot-synth all present",
    )


def _check_npx() -> CheckResult:
    npx = shutil.which("npx")
    if npx is None:
        return CheckResult(
            status=CHECK_WARN,
            title="npx on PATH",
            detail="not found — required only if you plan to proxy the "
                   "upstream filesystem MCP server.",
            fix="Install Node 18+; npx ships with it.",
        )
    # Try `npx --version`
    try:
        v = subprocess.run(
            [npx, "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return CheckResult(
            status=CHECK_OK, title="npx on PATH", detail=f"{npx} (v{v})"
        )
    except Exception:
        return CheckResult(
            status=CHECK_WARN, title="npx on PATH",
            detail=f"{npx} exists but didn't respond to --version",
        )


def _check_db() -> CheckResult:
    try:
        from planckbot.config import config
        from planckbot.db.engine import get_connection
        from planckbot.db.migrations import SCHEMA_VERSION
        conn = get_connection(config.db_path)
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = int(row[0]) if row and row[0] is not None else 0
        if current < SCHEMA_VERSION:
            return CheckResult(
                status=CHECK_FAIL,
                title="DB schema version",
                detail=f"at v{current}, needs v{SCHEMA_VERSION}",
                fix="Re-run `planckbot init` — it re-applies migrations.",
            )
        return CheckResult(
            status=CHECK_OK,
            title="DB schema version",
            detail=f"v{current} at {config.db_path}",
        )
    except Exception as e:
        return CheckResult(
            status=CHECK_FAIL,
            title="DB schema version",
            detail=f"cannot open / migrate: {e}",
            fix="Run `planckbot init`.",
        )


def _check_claude_config() -> CheckResult:
    cfg_path = Path.home() / ".claude.json"
    if not cfg_path.exists():
        return CheckResult(
            status=CHECK_WARN,
            title="Claude Code config",
            detail=f"{cfg_path} does not exist",
            fix="Run `planckbot init --upstream-path /abs/path/to/your/project`.",
        )
    try:
        cfg = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        return CheckResult(
            status=CHECK_FAIL,
            title="Claude Code config",
            detail=f"~/.claude.json is not valid JSON: {e}",
            fix="Hand-fix the JSON and re-run `planckbot doctor`.",
        )
    servers = cfg.get("mcpServers", {}) or {}
    has_fs = "planckbot-fs" in servers
    has_synth = "planckbot-synth" in servers
    if not has_fs and not has_synth:
        return CheckResult(
            status=CHECK_WARN,
            title="Claude Code config",
            detail="neither planckbot-fs nor planckbot-synth is registered",
            fix="Run `planckbot init --upstream-path /abs/path`.",
        )
    have = [name for name, present in
            (("planckbot-fs", has_fs), ("planckbot-synth", has_synth))
            if present]
    return CheckResult(
        status=CHECK_OK,
        title="Claude Code config",
        detail=f"registered MCP servers: {', '.join(have)}",
    )


def _check_mcp_processes() -> CheckResult:
    from planckbot.ui.mcp_status import read_mcp_status
    st = read_mcp_status()
    if not st.configured:
        return CheckResult(
            status=CHECK_WARN,
            title="MCP subprocesses",
            detail="planckbot-fs not configured, so nothing to run",
        )
    if not st.fs_running:
        return CheckResult(
            status=CHECK_WARN,
            title="MCP subprocesses",
            detail="planckbot-fs not currently running",
            fix="Restart Claude Code — it spawns the MCP subprocess on start.",
        )
    return CheckResult(
        status=CHECK_OK,
        title="MCP subprocesses",
        detail=f"planckbot-fs live ({len(st.fs_pids)} proc), "
               f"mode={st.mode or 'observe'}",
    )


def _check_adapters() -> CheckResult:
    try:
        from planckbot.config import config
        from planckbot.db.engine import get_connection
        from planckbot.models.checkpoints import CheckpointManager
        conn = get_connection(config.db_path)
        mgr = CheckpointManager(conn)
        total = mgr.count()
        active = conn.execute(
            "SELECT COUNT(*) FROM model_checkpoints WHERE is_active = 1"
        ).fetchone()[0]
        if total == 0:
            return CheckResult(
                status=CHECK_WARN,
                title="Trained adapters",
                detail="no checkpoints yet — expected for a fresh install",
                fix="Train one with `planckbot train --tool <name> "
                    "--fixture <path> --activate` after you've collected triples.",
            )
        return CheckResult(
            status=CHECK_OK,
            title="Trained adapters",
            detail=f"{total} checkpoint(s), {active} active",
        )
    except Exception as e:
        return CheckResult(
            status=CHECK_WARN,
            title="Trained adapters",
            detail=f"could not query: {e}",
        )


def _check_systemd_unit() -> CheckResult:
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return CheckResult(
            status=CHECK_OK,
            title="Cron daemon (systemd)",
            detail="systemctl not available on this system — use "
                   "`planckbot cron daemon` in a tmux session instead.",
        )
    try:
        res = subprocess.run(
            [systemctl, "--user", "is-active", "planckbot-cron.service"],
            capture_output=True, text=True, timeout=3,
        )
        status = res.stdout.strip()
        if status == "active":
            return CheckResult(
                status=CHECK_OK,
                title="Cron daemon (systemd)",
                detail="planckbot-cron.service is active",
            )
        if status in {"inactive", "failed"}:
            return CheckResult(
                status=CHECK_WARN,
                title="Cron daemon (systemd)",
                detail=f"planckbot-cron.service is {status}",
                fix="Run `planckbot systemd install` to enable and start it.",
            )
        # Unit not installed (systemctl prints 'inactive' for unknown too
        # depending on version — fall through).
        return CheckResult(
            status=CHECK_WARN,
            title="Cron daemon (systemd)",
            detail=f"planckbot-cron.service state: {status or '(empty)'}",
            fix="Run `planckbot systemd install` to enable and start it.",
        )
    except Exception:
        return CheckResult(
            status=CHECK_WARN,
            title="Cron daemon (systemd)",
            detail="could not query systemctl",
        )


def _check_cron_jobs() -> CheckResult:
    try:
        from planckbot.config import config
        from planckbot.cron.store import CronStore
        from planckbot.db.engine import get_connection
        conn = get_connection(config.db_path)
        jobs = CronStore(conn).list_all()
        n = len(jobs)
        n_enabled = sum(1 for j in jobs if j.enabled)
        if n == 0:
            return CheckResult(
                status=CHECK_WARN,
                title="Scheduled jobs",
                detail="no cron jobs scheduled",
                fix="Suggested minimum: an `autolabel_precise` job per tool "
                    "you want to supervise + a `detect_tool_gaps` job hourly.",
            )
        return CheckResult(
            status=CHECK_OK,
            title="Scheduled jobs",
            detail=f"{n_enabled}/{n} enabled",
        )
    except Exception as e:
        return CheckResult(
            status=CHECK_WARN,
            title="Scheduled jobs",
            detail=f"could not query: {e}",
        )


def _check_data_dir() -> CheckResult:
    from planckbot.config import config
    d = config.data_dir
    if not d.exists():
        return CheckResult(
            status=CHECK_FAIL,
            title="Data directory",
            detail=f"{d} does not exist",
            fix="Run `planckbot init`.",
        )
    subdirs = [
        d / "checkpoints",
        d / "exports",
        d / "fixtures",
    ]
    missing = [s for s in subdirs if not s.exists()]
    if missing:
        return CheckResult(
            status=CHECK_WARN,
            title="Data directory",
            detail=f"missing subdirs: {[s.name for s in missing]}",
            fix="Run `planckbot init` to (re-)create them.",
        )
    return CheckResult(
        status=CHECK_OK,
        title="Data directory",
        detail=f"{d} is well-formed",
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


CHECKS = [
    _check_python_version,
    _check_in_venv,
    _check_package_installed,
    _check_binaries,
    _check_npx,
    _check_data_dir,
    _check_db,
    _check_claude_config,
    _check_mcp_processes,
    _check_adapters,
    _check_cron_jobs,
    _check_systemd_unit,
]


def run_checks() -> list[CheckResult]:
    out: list[CheckResult] = []
    for fn in CHECKS:
        try:
            out.append(fn())
        except Exception as e:  # pragma: no cover — defensive
            out.append(CheckResult(
                status=CHECK_FAIL,
                title=fn.__name__.lstrip("_").replace("_", " "),
                detail=f"check crashed: {e}",
            ))
    return out


def worst(results: list[CheckResult]) -> str:
    if any(r.status == CHECK_FAIL for r in results):
        return CHECK_FAIL
    if any(r.status == CHECK_WARN for r in results):
        return CHECK_WARN
    return CHECK_OK
