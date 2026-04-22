"""Experiment comparison table component."""

from nicegui import ui
from planckbot.db.models import Experiment
from planckbot.ui.theme import COLORS
from planckbot.ui.components.status_badge import status_badge


def comparison_table(experiments: list[Experiment]):
    """Side-by-side experiment comparison."""
    if not experiments:
        ui.label("No experiments to compare.").style(f"color: {COLORS['text_muted']};")
        return

    columns = [
        {"name": "field", "label": "Field", "field": "field", "align": "left"},
    ]
    for i, exp in enumerate(experiments):
        columns.append({
            "name": f"exp_{i}",
            "label": exp.name,
            "field": f"exp_{i}",
            "align": "left",
        })

    fields = [
        ("Name", lambda e: e.name),
        ("Type", lambda e: e.experiment_type),
        ("Status", lambda e: e.status),
        ("Hypothesis", lambda e: e.hypothesis or "—"),
        ("Model", lambda e: (e.config or {}).get("model_name", "—")),
        ("Tool", lambda e: (e.config or {}).get("tool_name", "—")),
        ("Strategy", lambda e: (e.config or {}).get("strategy", "—")),
        ("Token Savings", lambda e: f"{(e.metrics or {}).get('token_savings_pct', '—')}%"),
        ("Accuracy", lambda e: str((e.metrics or {}).get("accuracy", "—"))),
        ("Latency (ms)", lambda e: str((e.metrics or {}).get("latency_ms", "—"))),
        ("Created", lambda e: e.created_at[:16] if e.created_at else "—"),
    ]

    rows = []
    for label, fn in fields:
        row = {"field": label}
        for i, exp in enumerate(experiments):
            row[f"exp_{i}"] = fn(exp)
        rows.append(row)

    ui.table(
        columns=columns, rows=rows, row_key="field",
    ).style(
        f"background-color: {COLORS['surface']}; color: {COLORS['text']}; width: 100%;"
    ).classes("comparison-table")
