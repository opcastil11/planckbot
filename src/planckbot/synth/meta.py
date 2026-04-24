"""Layer D meta-tool: create / activate / retire synthesized tools."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from planckbot.db.models import SynthesizedTool
from planckbot.tools.meta import _validate as _validate_ast


# We drop synthesized tool code under this directory so a hot-reload just
# means "re-scan the directory". Kept under `data/` so it's gitignored along
# with checkpoints + DB.
DEFAULT_SYNTH_DIR = Path("data/synthesized_tools")


# --- store -----------------------------------------------------------------


class SynthesizedToolStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, tool: SynthesizedTool) -> SynthesizedTool:
        row = tool.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO synthesized_tools ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return tool

    def get(self, tool_id: str) -> SynthesizedTool | None:
        cur = self.conn.execute(
            "SELECT * FROM synthesized_tools WHERE id = ?", (tool_id,)
        )
        row = cur.fetchone()
        return SynthesizedTool.from_row(row) if row else None

    def by_name(self, name: str) -> SynthesizedTool | None:
        cur = self.conn.execute(
            "SELECT * FROM synthesized_tools WHERE name = ?", (name,)
        )
        row = cur.fetchone()
        return SynthesizedTool.from_row(row) if row else None

    def list_all(self, project_id: str | None = None) -> list[SynthesizedTool]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM synthesized_tools ORDER BY created_at DESC"
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM synthesized_tools "
                "WHERE project_id = ? OR project_id IS NULL "
                "ORDER BY created_at DESC",
                (project_id,),
            )
        return [SynthesizedTool.from_row(r) for r in cur.fetchall()]

    def list_active(
        self, project_id: str | None = None
    ) -> list[SynthesizedTool]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM synthesized_tools WHERE status = 'active' "
                "ORDER BY created_at ASC"
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM synthesized_tools WHERE status = 'active' "
                "  AND (project_id = ? OR project_id IS NULL) "
                "ORDER BY created_at ASC",
                (project_id,),
            )
        return [SynthesizedTool.from_row(r) for r in cur.fetchall()]

    def set_status(self, tool_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE synthesized_tools SET status = ? WHERE id = ?",
            (status, tool_id),
        )
        self.conn.commit()

    def delete(self, tool_id: str) -> None:
        self.conn.execute(
            "DELETE FROM synthesized_tools WHERE id = ?", (tool_id,)
        )
        self.conn.commit()

    def count(self, project_id: str | None = None) -> int:
        if project_id is None:
            return self.conn.execute(
                "SELECT COUNT(*) FROM synthesized_tools"
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM synthesized_tools "
            "WHERE project_id = ? OR project_id IS NULL",
            (project_id,),
        ).fetchone()[0]


# --- meta-tool -------------------------------------------------------------


@dataclass
class SynthesisResult:
    tool: SynthesizedTool
    source_path: Path


def synthesize_tool(
    *,
    name: str,
    description: str,
    input_schema: dict,
    code: str,
    conn: sqlite3.Connection,
    gap_report_id: str | None = None,
    created_by: str = "agent:claude",
    synth_dir: Path | None = None,
    project_id: str | None = None,
) -> SynthesisResult:
    """Register a brand-new tool built from usage observations.

    `code` must define a module-level function whose name matches `name`;
    the function's signature should line up with `input_schema`. The AST
    whitelist used by `edit_tool` rejects forbidden imports/attrs up front,
    so `code` can't pull in `subprocess`, call `os.system`, etc.

    The tool lands in the DB as `status='draft'` — not served to Claude
    until `activate_tool(name)` flips it.
    """
    _validate_name(name)
    _validate_code_defines_function(code, name)
    # Layer-D synthesis: the tool is brand-new, so there's no pre-existing
    # dependency surface to compare against. We still enforce the forbidden
    # imports + forbidden names + forbidden attrs, just not "no new imports".
    _validate_ast("", code, forbid_new_imports=False)

    store = SynthesizedToolStore(conn)
    if store.by_name(name) is not None:
        raise ValueError(f"a synthesized tool named {name!r} already exists")

    out_dir = synth_dir or DEFAULT_SYNTH_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = out_dir / f"{name}.py"
    source_path.write_text(code)

    tool = SynthesizedTool(
        name=name,
        description=description,
        input_schema=input_schema or {},
        code=code,
        source_file_path=str(source_path),
        status="draft",
        created_by=created_by,
        gap_report_id=gap_report_id,
        project_id=project_id,
    )
    store.insert(tool)
    return SynthesisResult(tool=tool, source_path=source_path)


def activate_tool(
    name: str,
    *,
    conn: sqlite3.Connection,
    hot_reload: bool = True,
    pid_file: Path | None = None,
) -> SynthesizedTool:
    """Flip a tool to `status='active'` and (best-effort) SIGHUP the MCP
    server so it picks it up without a restart."""
    store = SynthesizedToolStore(conn)
    tool = store.by_name(name)
    if tool is None:
        raise ValueError(f"no synthesized tool named {name!r}")
    store.set_status(tool.id, "active")
    tool.status = "active"
    if hot_reload:
        _signal_synth_server(pid_file)
    return tool


def deactivate_tool(
    name: str,
    *,
    conn: sqlite3.Connection,
    hot_reload: bool = True,
    pid_file: Path | None = None,
) -> SynthesizedTool:
    store = SynthesizedToolStore(conn)
    tool = store.by_name(name)
    if tool is None:
        raise ValueError(f"no synthesized tool named {name!r}")
    store.set_status(tool.id, "retired")
    tool.status = "retired"
    if hot_reload:
        _signal_synth_server(pid_file)
    return tool


# --- helpers ---------------------------------------------------------------


def _validate_name(name: str) -> None:
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(
            f"synthesized tool name must be [A-Za-z0-9_]+, got {name!r}"
        )
    if name[0].isdigit():
        raise ValueError("synthesized tool name cannot start with a digit")


def _validate_code_defines_function(code: str, name: str) -> None:
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"synthesized code has syntax error: {e}") from e
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return
    raise ValueError(
        f"synthesized code must define a top-level function named {name!r}"
    )


DEFAULT_SYNTH_PID_FILE = Path("/tmp/planckbot-synth.pid")


def _signal_synth_server(pid_file: Path | None) -> None:
    """Send SIGHUP to the running synth MCP server so it reloads. Silent
    no-op if no server is running."""
    pf = pid_file or DEFAULT_SYNTH_PID_FILE
    if not pf.exists():
        return
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, signal.SIGHUP)
    except (ValueError, ProcessLookupError, PermissionError):
        # Stale pid file — leave it, next server start will overwrite.
        return


def load_function_from_code(code: str, name: str) -> Callable:
    """Exec `code` in a fresh module namespace and return the function
    named `name`. The AST whitelist has already been enforced by
    `synthesize_tool`; this is the "run the code" step the MCP server uses
    at dispatch time.
    """
    ns: dict = {"__name__": f"planckbot_synth_{name}"}
    exec(compile(code, f"<synth:{name}>", "exec"), ns, ns)
    fn = ns.get(name)
    if fn is None or not callable(fn):
        raise ValueError(
            f"code does not define a callable named {name!r} at top level"
        )
    return fn
