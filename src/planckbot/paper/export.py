"""Generate paper-ready artifacts: tables, figures, reports."""

import json
from datetime import datetime, timezone
from pathlib import Path

from planckbot.db.models import Experiment, PaperEntry


def export_experiment_table(experiments: list[Experiment]) -> str:
    """Generate a markdown comparison table."""
    if not experiments:
        return "*No experiments to compare.*"

    header = "| Experiment | Model | Tool | Strategy | Token Savings | Accuracy | Latency (ms) | Status |"
    sep = "|---|---|---|---|---|---|---|---|"
    rows = []

    for exp in experiments:
        cfg = exp.config or {}
        met = exp.metrics or {}
        rows.append(
            f"| {exp.name} "
            f"| {cfg.get('model_name', 'N/A')} "
            f"| {cfg.get('tool_name', 'N/A')} "
            f"| {cfg.get('strategy', 'N/A')} "
            f"| {met.get('token_savings_pct', 'N/A')}% "
            f"| {met.get('accuracy', 'N/A')} "
            f"| {met.get('latency_ms', 'N/A')} "
            f"| {exp.status} |"
        )

    return "\n".join([header, sep] + rows)


def export_full_report(
    experiments: list[Experiment],
    entries: list[PaperEntry],
    output_path: Path | None = None,
) -> str:
    """Generate a full markdown report for the paper."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections = [
        f"# PlanckBot Experiment Report",
        f"Generated: {now}\n",
        "## Experiment Summary\n",
        export_experiment_table(experiments),
        "\n## Research Log\n",
    ]

    for entry in sorted(entries, key=lambda e: e.created_at):
        icon = {
            "observation": "eye",
            "result": "check",
            "decision": "scales",
            "question": "question",
        }.get(entry.entry_type, "note")

        sections.append(f"### [{entry.entry_type}] {entry.title}")
        sections.append(f"*{entry.created_at[:10]}*")
        if entry.tags:
            sections.append(f"Tags: {', '.join(entry.tags)}")
        sections.append(f"\n{entry.content}\n")
        sections.append("---\n")

    report = "\n".join(sections)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)

    return report
