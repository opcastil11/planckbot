"""Blocking-loop cron daemon.

The daemon is the single execution site for scheduled jobs. Start one process
(from the CLI or systemd user unit) and it will keep `next_run_at` honest
forever. File-locked so a second daemon started by accident can't double-fire.
"""

from __future__ import annotations

import fcntl
import logging
import signal
import sqlite3
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from planckbot.cron.jobs import JobContext, JobRegistry, default_registry
from planckbot.cron.store import CronStore


logger = logging.getLogger(__name__)


class Daemon:
    """Simple polling scheduler.

    Dispatches one job at a time. Jobs that take a long time will push other
    jobs' fire times later; for the PlanckBot workload (autolabel in seconds,
    retrain gated behind `min_new_labeled`) that's acceptable. Throwing a
    thread-pool at this is a fine follow-up but currently over-engineered.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        tick_seconds: float = 2.0,
        registry: JobRegistry | None = None,
        lock_path: str | Path | None = None,
    ):
        self.conn = conn
        self.store = CronStore(conn)
        self.registry = registry or default_registry()
        self.tick_seconds = tick_seconds
        self.lock_path = Path(lock_path) if lock_path else Path("/tmp/planckbot-cron.lock")
        self._stopping = False
        self._lock_fh = None

    def _acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Open in append mode so we don't truncate a live owner's pid.
        self._lock_fh = open(self.lock_path, "a+")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                f"another PlanckBot cron daemon already holds {self.lock_path}"
            )
        self._lock_fh.seek(0)
        self._lock_fh.truncate()
        import os
        self._lock_fh.write(f"{os.getpid()}\n")
        self._lock_fh.flush()

    def _release_lock(self) -> None:
        if self._lock_fh is not None:
            try:
                fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
            finally:
                self._lock_fh.close()
                self._lock_fh = None

    def stop(self, *_) -> None:
        self._stopping = True

    def run(self) -> None:
        """Block forever (or until SIGTERM/SIGINT) dispatching due jobs."""
        self._acquire_lock()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("cron daemon up, tick=%.1fs, lock=%s",
                    self.tick_seconds, self.lock_path)
        try:
            while not self._stopping:
                self._tick()
                # Sleep in small increments so stop() kicks in quickly.
                slept = 0.0
                while slept < self.tick_seconds and not self._stopping:
                    time.sleep(0.25)
                    slept += 0.25
        finally:
            self._release_lock()
            logger.info("cron daemon down")

    def _tick(self) -> None:
        """Run every due job once."""
        now = datetime.now(timezone.utc)
        try:
            due = self.store.list_due(now)
        except sqlite3.Error:
            logger.exception("failed to list due jobs")
            return

        for job in due:
            self.run_job(job.id)

    def run_job(self, job_id: str) -> tuple[str, str]:
        """Dispatch a single job by id. Returns (status, output)."""
        from planckbot import activity
        import time

        job = self.store.get(job_id)
        if job is None:
            return ("error", f"job {job_id} not found")
        fn = self.registry.get(job.job_type)
        if fn is None:
            output = f"unknown job_type={job.job_type!r}"
            self.store.mark_run(job.id, status="error", output=output)
            activity.log_event(
                self.conn, "cron", "job_error",
                f"{job.name}: unknown job_type {job.job_type!r}",
                project_id=job.project_id,
                meta={"job": job.name, "job_type": job.job_type},
            )
            return ("error", output)

        activity.log_event(
            self.conn, "cron", "job_start",
            f"{job.name} ({job.job_type}) starting",
            project_id=job.project_id,
            meta={"job": job.name, "job_type": job.job_type},
        )
        ctx = JobContext(
            conn=self.conn, params=job.params, project_id=job.project_id,
        )
        t0 = time.time()
        try:
            output = fn(ctx)
            duration_ms = round((time.time() - t0) * 1000, 1)
            self.store.mark_run(job.id, status="ok", output=output or "")
            activity.log_event(
                self.conn, "cron", "job_end",
                f"{job.name} ok in {duration_ms} ms",
                project_id=job.project_id,
                meta={"job": job.name, "job_type": job.job_type,
                      "duration_ms": duration_ms},
            )
            return ("ok", output or "")
        except Exception as e:
            duration_ms = round((time.time() - t0) * 1000, 1)
            output = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            logger.warning("job %s (%s) failed: %s", job.name, job.job_type, e)
            self.store.mark_run(job.id, status="error", output=output)
            activity.log_event(
                self.conn, "cron", "job_error",
                f"{job.name} failed: {type(e).__name__}: {e}",
                project_id=job.project_id,
                meta={"job": job.name, "job_type": job.job_type,
                      "duration_ms": duration_ms,
                      "exception": type(e).__name__},
            )
            return ("error", output)


SYSTEMD_UNIT_NAME = "planckbot-cron.service"


def systemd_available() -> bool:
    """True if `systemctl` is on PATH and the unit file exists."""
    import shutil
    if shutil.which("systemctl") is None:
        return False
    unit = Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME
    return unit.exists()


def systemd_status() -> dict:
    """Probe `systemctl --user` for the cron unit. Returns:
        available  bool — systemctl + unit file present
        active     bool — `is-active` says "active"
        enabled    bool — `is-enabled` says "enabled"
        sub        str  — sub-state ("running", "dead", "failed", …) or ""
    """
    info = {"available": False, "active": False, "enabled": False, "sub": ""}
    if not systemd_available():
        return info
    info["available"] = True
    import subprocess
    try:
        r1 = subprocess.run(
            ["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME],
            capture_output=True, text=True, timeout=3,
        )
        info["sub"] = (r1.stdout or "").strip()
        info["active"] = info["sub"] == "active"
        r2 = subprocess.run(
            ["systemctl", "--user", "is-enabled", SYSTEMD_UNIT_NAME],
            capture_output=True, text=True, timeout=3,
        )
        info["enabled"] = (r2.stdout or "").strip() == "enabled"
    except (subprocess.TimeoutExpired, OSError):
        pass
    return info


def systemd_action(action: str) -> tuple[bool, str]:
    """Run `systemctl --user <action> planckbot-cron.service`. Returns
    (ok, message). Whitelisted to start/stop/restart for safety; never
    accept arbitrary action strings from a UI handler."""
    if action not in ("start", "stop", "restart"):
        return False, f"unsupported action: {action!r}"
    if not systemd_available():
        return False, "systemctl unavailable or unit not installed"
    import subprocess
    try:
        r = subprocess.run(
            ["systemctl", "--user", action, SYSTEMD_UNIT_NAME],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"systemctl error: {e}"
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return False, msg
    return True, f"{action} ok"


def daemon_status(
    lock_path: str | Path | None = None,
) -> dict:
    """Probe whether a daemon is currently running.

    Strategy: try to acquire a *non-exclusive* lock on the same file the
    daemon takes. If `flock` blocks, someone else owns it → daemon alive.
    Read the pid from the file for display.

    Returns a dict:
        running    bool
        pid        int | None
        lock_path  str
        last_mtime str | None  (ISO of the lockfile mtime, useful as heartbeat)
    """
    p = Path(lock_path) if lock_path else Path("/tmp/planckbot-cron.lock")
    info: dict = {
        "running": False,
        "pid": None,
        "lock_path": str(p),
        "last_mtime": None,
    }
    if not p.exists():
        return info

    try:
        info["last_mtime"] = datetime.fromtimestamp(
            p.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    except OSError:
        pass

    try:
        with open(p, "r") as fh:
            try:
                # Non-blocking shared lock: if it fails the daemon holds an
                # exclusive lock.
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                info["running"] = False
            except BlockingIOError:
                info["running"] = True
            try:
                fh.seek(0)
                pid_line = fh.readline().strip()
                if pid_line:
                    info["pid"] = int(pid_line)
            except (OSError, ValueError):
                pass
    except OSError:
        return info

    # Sanity-check: if we read a pid but the process is gone, lock was stale.
    if info["pid"] is not None and not info["running"]:
        info["pid"] = None
    return info
