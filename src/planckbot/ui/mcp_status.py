"""Inspect where and how PlanckBot is currently connected.

The user's first question looking at the dashboard is usually "is this
thing actually plugged into Claude Code, and if so, what is it watching?"
This module answers that by reading ~/.claude.json and checking for
running MCP subprocesses.

Pure observation — we do not mutate anything here.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


CLAUDE_CONFIG = Path.home() / ".claude.json"


@dataclass
class MCPStatus:
    configured: bool = False         # planckbot-fs present in claude.json
    config_error: str | None = None  # non-None when claude.json is malformed
    upstream_path: str | None = None # path the filesystem MCP serves
    mode: str | None = None          # observe | suggest | intervene
    threshold: float | None = None   # confidence threshold (intervene)
    strategy: str | None = None      # filter_output | compress_input | ...
    synth_configured: bool = False   # planckbot-synth present in claude.json
    fs_pids: list[str] = field(default_factory=list)
    synth_pids: list[str] = field(default_factory=list)

    # Convenience derived fields ------------------------------------------

    @property
    def fs_running(self) -> bool:
        return len(self.fs_pids) > 0

    @property
    def synth_running(self) -> bool:
        return len(self.synth_pids) > 0

    @property
    def fs_healthy(self) -> bool:
        return self.configured and self.fs_running

    @property
    def synth_healthy(self) -> bool:
        return self.synth_configured and self.synth_running

    @property
    def short_summary(self) -> str:
        """One-line summary for compact UI surfaces (sidebar chip)."""
        if self.config_error:
            return "config invalid"
        if not self.configured:
            return "not connected"
        short_path = self._short_path()
        if not self.fs_running:
            return f"{short_path} · idle"
        return f"{short_path} · {self.mode or 'observe'}"

    def _short_path(self) -> str:
        if not self.upstream_path:
            return "(no path)"
        p = self.upstream_path
        # Last two path components, truncated
        parts = [x for x in p.split("/") if x]
        tail = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else p)
        return f".../{tail}" if len(parts) > 2 else f"/{tail}"


def _parse_fs_args(args: list) -> tuple[str | None, str | None, float | None, str | None]:
    """Pull (upstream_path, mode, threshold, strategy) out of a planckbot-fs
    args vector. The args format is:

        --mode observe --name planckbot-fs [--threshold 0.9] [--strategy X]
        -- <cmd> <arg...> <upstream_path>

    We trust the argparse shape our own CLI defines, but we're defensive."""
    mode = None
    threshold = None
    strategy = None
    upstream_path = None

    it = iter(range(len(args)))
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            i += 2
            continue
        if tok == "--threshold" and i + 1 < len(args):
            try:
                threshold = float(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if tok == "--strategy" and i + 1 < len(args):
            strategy = args[i + 1]
            i += 2
            continue
        if tok == "--":
            # The remainder is <cmd> <args...>. The standard filesystem
            # server takes the served path as its LAST positional.
            after = args[i + 1 :]
            if after:
                upstream_path = after[-1]
            break
        i += 1

    return upstream_path, mode, threshold, strategy


def _pgrep(pattern: str) -> list[str]:
    """Return PIDs matching pattern (empty on any failure)."""
    try:
        res = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pids: list[str] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(line)
    return pids


def read_mcp_status(*, claude_config: Path | None = None) -> MCPStatus:
    """Look at ~/.claude.json and at running processes; return a snapshot."""
    status = MCPStatus()
    path = claude_config or CLAUDE_CONFIG

    if path.exists():
        try:
            cfg = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            status.config_error = f"invalid JSON: {e}"
            return status
    else:
        status.config_error = f"{path} not found"
        return status

    servers = cfg.get("mcpServers", {}) or {}
    fs_entry = servers.get("planckbot-fs")
    synth_entry = servers.get("planckbot-synth")

    if fs_entry:
        status.configured = True
        args = fs_entry.get("args") or []
        upstream, mode, threshold, strategy = _parse_fs_args(args)
        status.upstream_path = upstream
        status.mode = mode
        status.threshold = threshold
        status.strategy = strategy

    if synth_entry:
        status.synth_configured = True

    # Running processes. `bin/planckbot-mcp` catches the fs proxy and
    # `bin/planckbot-synth` catches the synth server, without also matching
    # the `planckbot` UI process.
    status.fs_pids = _pgrep("bin/planckbot-mcp")
    status.synth_pids = _pgrep("bin/planckbot-synth")

    return status
