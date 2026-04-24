"""Track model checkpoints (adapters) on disk and in DB."""

import json
import sqlite3
from pathlib import Path

from planckbot.db.models import ModelCheckpoint


class CheckpointManager:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, checkpoint: ModelCheckpoint) -> ModelCheckpoint:
        row = checkpoint.to_row()
        cols = ", ".join(row.keys())
        ph = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO model_checkpoints ({cols}) VALUES ({ph})",
            list(row.values()),
        )
        self.conn.commit()
        return checkpoint

    def get(self, ckpt_id: str) -> ModelCheckpoint | None:
        cur = self.conn.execute("SELECT * FROM model_checkpoints WHERE id = ?", (ckpt_id,))
        row = cur.fetchone()
        return ModelCheckpoint.from_row(row) if row else None

    def list_all(
        self,
        tool_name: str | None = None,
        limit: int = 100,
        project_id: str | None = None,
    ) -> list[ModelCheckpoint]:
        clauses: list[str] = []
        args: list = []
        if tool_name:
            clauses.append("tool_name = ?")
            args.append(tool_name)
        if project_id is not None:
            clauses.append("project_id = ?")
            args.append(project_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(limit)
        cur = self.conn.execute(
            f"SELECT * FROM model_checkpoints {where} "
            "ORDER BY created_at DESC LIMIT ?",
            args,
        )
        return [ModelCheckpoint.from_row(r) for r in cur.fetchall()]

    def get_active(
        self, tool_name: str, project_id: str | None = None
    ) -> ModelCheckpoint | None:
        """Active checkpoint for a tool. When `project_id` is supplied, the
        lookup is scoped to that project; otherwise any project (or legacy
        NULL) wins. The proxy passes its active-project id at startup so
        adapters trained on one repo don't leak into another."""
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM model_checkpoints "
                "WHERE tool_name = ? AND is_active = 1",
                (tool_name,),
            )
        else:
            # Prefer an adapter trained on this project; fall back to the
            # unscoped (legacy) row so pre-v6 adapters still serve until
            # retrained per-project.
            cur = self.conn.execute(
                "SELECT * FROM model_checkpoints "
                "WHERE tool_name = ? AND is_active = 1 "
                "  AND (project_id = ? OR project_id IS NULL) "
                "ORDER BY (project_id = ?) DESC LIMIT 1",
                (tool_name, project_id, project_id),
            )
        row = cur.fetchone()
        return ModelCheckpoint.from_row(row) if row else None

    def activate(self, ckpt_id: str, *, require_blessed: bool = True):
        """Mark a checkpoint active for its tool.

        Since schema v5, an un-blessed checkpoint is rejected by default
        to prevent accidentally activating a regressive adapter. Pass
        `require_blessed=False` only from trusted contexts (tests, or an
        operator who explicitly knows what they're doing via the CLI
        `--unsafe` flag). The proxy layer ALSO double-checks blessed at
        call time before applying the adapter's output.
        """
        ckpt = self.get(ckpt_id)
        if not ckpt:
            raise ValueError(f"checkpoint not found: {ckpt_id}")
        if require_blessed and not ckpt.blessed:
            raise ValueError(
                f"checkpoint {ckpt_id[:8]} ({ckpt.name}) is not blessed — "
                "run `planckbot bless <ckpt_id>` first, or pass "
                "require_blessed=False explicitly."
            )
        # Deactivate other checkpoints for the same (tool, project) pair.
        # A checkpoint scoped to project A doesn't interfere with project B's
        # active adapter for the same tool — that's the whole point of
        # per-project scoping. Legacy NULL-project checkpoints are treated
        # as a scope of their own.
        if ckpt.tool_name:
            if ckpt.project_id is None:
                self.conn.execute(
                    "UPDATE model_checkpoints SET is_active = 0 "
                    "WHERE tool_name = ? AND project_id IS NULL",
                    (ckpt.tool_name,),
                )
            else:
                self.conn.execute(
                    "UPDATE model_checkpoints SET is_active = 0 "
                    "WHERE tool_name = ? AND project_id = ?",
                    (ckpt.tool_name, ckpt.project_id),
                )
        self.conn.execute(
            "UPDATE model_checkpoints SET is_active = 1 WHERE id = ?", (ckpt_id,)
        )
        self.conn.commit()

    def deactivate(self, ckpt_id: str):
        self.conn.execute(
            "UPDATE model_checkpoints SET is_active = 0 WHERE id = ?", (ckpt_id,)
        )
        self.conn.commit()

    def bless(self, ckpt_id: str, *, tuned_threshold: float | None = None) -> None:
        """Explicitly mark a checkpoint safe to serve. The operator is
        asserting that they have evaluated the adapter (e.g. with
        `planckbot proxy-demo`) and it actually compresses instead of
        regressing. Optional per-checkpoint threshold overrides the
        proxy's global default."""
        if tuned_threshold is not None and not (0.0 <= tuned_threshold <= 1.0):
            raise ValueError(
                f"tuned_threshold out of range [0, 1]: {tuned_threshold}"
            )
        if tuned_threshold is None:
            self.conn.execute(
                "UPDATE model_checkpoints SET blessed = 1 WHERE id = ?",
                (ckpt_id,),
            )
        else:
            self.conn.execute(
                "UPDATE model_checkpoints SET blessed = 1, "
                "tuned_threshold = ? WHERE id = ?",
                (tuned_threshold, ckpt_id),
            )
        self.conn.commit()

    def unbless(self, ckpt_id: str) -> None:
        """Revoke the blessed flag and deactivate the checkpoint. Used
        after an adapter is found to regress in production."""
        self.conn.execute(
            "UPDATE model_checkpoints SET blessed = 0, is_active = 0 "
            "WHERE id = ?",
            (ckpt_id,),
        )
        self.conn.commit()

    def delete(self, ckpt_id: str, delete_files: bool = False):
        if delete_files:
            ckpt = self.get(ckpt_id)
            if ckpt and ckpt.adapter_path:
                import shutil
                path = Path(ckpt.adapter_path)
                if path.exists():
                    shutil.rmtree(path)
        self.conn.execute("DELETE FROM model_checkpoints WHERE id = ?", (ckpt_id,))
        self.conn.commit()

    def count(self, project_id: str | None = None) -> int:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM model_checkpoints"
            )
        else:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM model_checkpoints WHERE project_id = ?",
                (project_id,),
            )
        return cur.fetchone()[0]

    def compare(self, ids: list[str]) -> list[ModelCheckpoint]:
        placeholders = ", ".join("?" for _ in ids)
        cur = self.conn.execute(
            f"SELECT * FROM model_checkpoints WHERE id IN ({placeholders})", ids
        )
        return [ModelCheckpoint.from_row(r) for r in cur.fetchall()]
