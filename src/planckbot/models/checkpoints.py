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

    def list_all(self, tool_name: str | None = None, limit: int = 100) -> list[ModelCheckpoint]:
        if tool_name:
            cur = self.conn.execute(
                "SELECT * FROM model_checkpoints WHERE tool_name = ? ORDER BY created_at DESC LIMIT ?",
                (tool_name, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM model_checkpoints ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [ModelCheckpoint.from_row(r) for r in cur.fetchall()]

    def get_active(self, tool_name: str) -> ModelCheckpoint | None:
        cur = self.conn.execute(
            "SELECT * FROM model_checkpoints WHERE tool_name = ? AND is_active = 1",
            (tool_name,),
        )
        row = cur.fetchone()
        return ModelCheckpoint.from_row(row) if row else None

    def activate(self, ckpt_id: str):
        ckpt = self.get(ckpt_id)
        if not ckpt:
            return
        # Deactivate all for same tool
        if ckpt.tool_name:
            self.conn.execute(
                "UPDATE model_checkpoints SET is_active = 0 WHERE tool_name = ?",
                (ckpt.tool_name,),
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

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM model_checkpoints")
        return cur.fetchone()[0]

    def compare(self, ids: list[str]) -> list[ModelCheckpoint]:
        placeholders = ", ".join("?" for _ in ids)
        cur = self.conn.execute(
            f"SELECT * FROM model_checkpoints WHERE id IN ({placeholders})", ids
        )
        return [ModelCheckpoint.from_row(r) for r in cur.fetchall()]
