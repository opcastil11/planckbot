"""Tests for database layer: engine, migrations, models."""

import json
import sqlite3

from planckbot.db.engine import get_connection
from planckbot.db.models import (
    Experiment, Triple, ModelCheckpoint, PaperEntry, MetricRecord, AgentEvent,
)


def test_connection_wal_mode(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_foreign_keys_enabled(conn):
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_schema_tables_exist(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cur.fetchall()}
    expected = {
        "schema_version", "experiments", "triples",
        "model_checkpoints", "paper_log", "metrics_history", "agent_events",
    }
    assert expected.issubset(tables)


def test_experiment_roundtrip(conn):
    exp = Experiment(name="test-1", hypothesis="h1", experiment_type="filter_output")
    row = exp.to_row()
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO experiments ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()

    cur = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp.id,))
    loaded = Experiment.from_row(cur.fetchone())
    assert loaded.name == "test-1"
    assert loaded.hypothesis == "h1"
    assert loaded.status == "planned"


def test_triple_roundtrip(conn):
    triple = Triple(
        tool_name="file_search",
        input_data='{"query":"test"}',
        output_data='{"results":[]}',
        input_tokens=5,
        output_tokens=4,
    )
    row = triple.to_row()
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO triples ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()

    cur = conn.execute("SELECT * FROM triples WHERE id = ?", (triple.id,))
    loaded = Triple.from_row(cur.fetchone())
    assert loaded.tool_name == "file_search"
    assert loaded.input_tokens == 5


def test_checkpoint_roundtrip(conn):
    ckpt = ModelCheckpoint(
        name="ckpt-1", base_model="SmolLM2-135M",
        lora_config={"r": 8, "alpha": 16},
        eval_metrics={"accuracy": 0.95},
    )
    row = ckpt.to_row()
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO model_checkpoints ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()

    cur = conn.execute("SELECT * FROM model_checkpoints WHERE id = ?", (ckpt.id,))
    loaded = ModelCheckpoint.from_row(cur.fetchone())
    assert loaded.lora_config["r"] == 8
    assert loaded.eval_metrics["accuracy"] == 0.95


def test_paper_entry_roundtrip(conn):
    entry = PaperEntry(
        title="Test observation",
        content="Something interesting",
        entry_type="observation",
        tags=["test", "baseline"],
    )
    row = entry.to_row()
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO paper_log ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()

    cur = conn.execute("SELECT * FROM paper_log WHERE id = ?", (entry.id,))
    loaded = PaperEntry.from_row(cur.fetchone())
    assert loaded.tags == ["test", "baseline"]


def test_metric_record_roundtrip(conn):
    rec = MetricRecord(
        experiment_id="fake-id", metric_name="loss", metric_value=0.42, step=10
    )
    row = rec.to_row()
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO metrics_history ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()

    cur = conn.execute("SELECT * FROM metrics_history WHERE id = ?", (rec.id,))
    loaded = MetricRecord.from_row(cur.fetchone())
    assert loaded.metric_value == 0.42
    assert loaded.step == 10


def test_experiment_config_json(conn):
    exp = Experiment(
        name="cfg-test",
        experiment_type="filter_output",
        config={"model_name": "SmolLM2-135M", "lr": 1e-4, "epochs": 3},
    )
    row = exp.to_row()
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO experiments ({cols}) VALUES ({ph})", list(row.values()))
    conn.commit()

    cur = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp.id,))
    loaded = Experiment.from_row(cur.fetchone())
    assert loaded.config["lr"] == 1e-4


def test_migration_idempotent(conn):
    """Running migrate again should not fail."""
    from planckbot.db.migrations import migrate
    migrate(conn)  # second call
    cur = conn.execute("SELECT COUNT(*) FROM schema_version")
    assert cur.fetchone()[0] == 1


def test_triple_index_exists(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_triples_tool'"
    )
    assert cur.fetchone() is not None
