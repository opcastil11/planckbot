"""CRUD for (input, context, output) triples."""

import json
import sqlite3
from typing import Any

from planckbot.db.models import Triple
from planckbot.experiments.metrics import count_tokens_approx


class TriplesStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(
        self,
        tool_name: str,
        input_data: Any,
        output_data: Any,
        context_data: Any = None,
        source: str = "manual",
        session_id: str | None = None,
        experiment_id: str | None = None,
        filtered_output: Any = None,
        project_id: str | None = None,
    ) -> Triple:
        input_str = json.dumps(input_data) if not isinstance(input_data, str) else input_data
        output_str = json.dumps(output_data) if not isinstance(output_data, str) else output_data
        context_str = json.dumps(context_data) if context_data and not isinstance(context_data, str) else context_data
        filtered_str = json.dumps(filtered_output) if filtered_output and not isinstance(filtered_output, str) else filtered_output

        triple = Triple(
            tool_name=tool_name,
            session_id=session_id,
            input_data=input_str,
            context_data=context_str,
            output_data=output_str,
            input_tokens=count_tokens_approx(input_str),
            output_tokens=count_tokens_approx(output_str),
            filtered_output=filtered_str,
            filtered_tokens=count_tokens_approx(filtered_str) if filtered_str else None,
            source=source,
            experiment_id=experiment_id,
            project_id=project_id,
        )
        row = triple.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO triples ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return triple

    def get(self, triple_id: str) -> Triple | None:
        cur = self.conn.execute("SELECT * FROM triples WHERE id = ?", (triple_id,))
        row = cur.fetchone()
        return Triple.from_row(row) if row else None

    def get_by_tool(
        self,
        tool_name: str,
        limit: int = 100,
        project_id: str | None = None,
    ) -> list[Triple]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM triples WHERE tool_name = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (tool_name, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM triples WHERE tool_name = ? AND project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (tool_name, project_id, limit),
            )
        return [Triple.from_row(r) for r in cur.fetchall()]

    def get_by_experiment(self, experiment_id: str) -> list[Triple]:
        cur = self.conn.execute(
            "SELECT * FROM triples WHERE experiment_id = ? ORDER BY created_at DESC",
            (experiment_id,),
        )
        return [Triple.from_row(r) for r in cur.fetchall()]

    def count_by_tool(
        self, project_id: str | None = None
    ) -> dict[str, int]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT tool_name, COUNT(*) as cnt FROM triples "
                "GROUP BY tool_name"
            )
        else:
            cur = self.conn.execute(
                "SELECT tool_name, COUNT(*) as cnt FROM triples "
                "WHERE project_id = ? GROUP BY tool_name",
                (project_id,),
            )
        return {row["tool_name"]: row["cnt"] for row in cur.fetchall()}

    def count_total(self, project_id: str | None = None) -> int:
        if project_id is None:
            cur = self.conn.execute("SELECT COUNT(*) FROM triples")
        else:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM triples WHERE project_id = ?",
                (project_id,),
            )
        return cur.fetchone()[0]

    def update_filtered(self, triple_id: str, filtered_output: str):
        filtered_tokens = count_tokens_approx(filtered_output)
        self.conn.execute(
            "UPDATE triples SET filtered_output = ?, filtered_tokens = ? WHERE id = ?",
            (filtered_output, filtered_tokens, triple_id),
        )
        self.conn.commit()

    def delete(self, triple_id: str):
        self.conn.execute("DELETE FROM triples WHERE id = ?", (triple_id,))
        self.conn.commit()

    def list_all(
        self, limit: int = 100, project_id: str | None = None
    ) -> list[Triple]:
        if project_id is None:
            cur = self.conn.execute(
                "SELECT * FROM triples ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM triples WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        return [Triple.from_row(r) for r in cur.fetchall()]

    def list_unlabeled(
        self,
        tool_name: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[Triple]:
        """Most recent triples with no filtered_output yet.

        Handy for auto-label workflows: take the newest N unlabeled triples
        for a given tool and pass them to `reference_tracker.label_triple_*`.
        """
        clauses = ["filtered_output IS NULL"]
        args: list = []
        if tool_name is not None:
            clauses.append("tool_name = ?")
            args.append(tool_name)
        if project_id is not None:
            clauses.append("project_id = ?")
            args.append(project_id)
        args.append(limit)
        cur = self.conn.execute(
            "SELECT * FROM triples WHERE " + " AND ".join(clauses) +
            " ORDER BY created_at DESC LIMIT ?",
            args,
        )
        return [Triple.from_row(r) for r in cur.fetchall()]

    def token_savings(
        self,
        source: str = "proxy:intervene",
        since: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, int]:
        """Aggregate token savings across triples where the tiny LLM intervened.

        Returns {raw_tokens, filtered_tokens, saved, intervene_count}. `saved`
        is raw - filtered and can be negative when the adapter regresses.
        """
        q = (
            "SELECT "
            "  COALESCE(SUM(output_tokens), 0) AS raw, "
            "  COALESCE(SUM(filtered_tokens), 0) AS filt, "
            "  COUNT(*) AS n "
            "FROM triples "
            "WHERE source = ? AND filtered_output IS NOT NULL "
            "  AND output_tokens IS NOT NULL AND filtered_tokens IS NOT NULL"
        )
        args: list = [source]
        if since is not None:
            q += " AND created_at >= ?"
            args.append(since)
        if project_id is not None:
            q += " AND project_id = ?"
            args.append(project_id)

        row = self.conn.execute(q, args).fetchone()
        raw = row["raw"] or 0
        filt = row["filt"] or 0
        return {
            "raw_tokens": int(raw),
            "filtered_tokens": int(filt),
            "saved": int(raw - filt),
            "intervene_count": int(row["n"] or 0),
        }
