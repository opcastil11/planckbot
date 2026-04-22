"""Dashboard page: metric cards, recent experiments, system info."""

from nicegui import ui
from planckbot.ui.theme import COLORS, CARD_STYLE
from planckbot.ui.components.metric_card import metric_card
from planckbot.ui.components.status_badge import status_badge
from planckbot.ui.state import get_state


def dashboard_page():
    state = get_state()

    # Metric cards row
    exp_count = state.experiments.count()
    triple_count = state.triples.count_total()
    ckpt_count = state.checkpoints.count()
    tool_count = len(state.registry.list_tools())

    with ui.row().classes("w-full gap-4 flex-wrap"):
        metric_card("Experiments", str(exp_count), icon="science", color=COLORS["primary"])
        metric_card("Triples", str(triple_count), icon="data_object", color=COLORS["info"])
        metric_card("Checkpoints", str(ckpt_count), icon="save", color=COLORS["success"])
        metric_card("Tools", str(tool_count), icon="build", color=COLORS["warning"])

    ui.separator().style(f"background: {COLORS['border']}; margin: 16px 0;")

    with ui.row().classes("w-full gap-4"):
        # Recent experiments table
        with ui.column().classes("flex-1"):
            ui.label("Recent Experiments").style(
                f"color: {COLORS['text']}; font-size: 18px; font-weight: 600; margin-bottom: 8px;"
            )
            recent = state.experiments.list_all(limit=10)
            if recent:
                columns = [
                    {"name": "name", "label": "Name", "field": "name", "align": "left"},
                    {"name": "type", "label": "Type", "field": "type", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    {"name": "created", "label": "Created", "field": "created", "align": "left"},
                ]
                rows = [
                    {
                        "name": e.name,
                        "type": e.experiment_type,
                        "status": e.status,
                        "created": e.created_at[:16] if e.created_at else "—",
                    }
                    for e in recent
                ]
                ui.table(columns=columns, rows=rows, row_key="name").style(
                    f"background-color: {COLORS['surface']}; color: {COLORS['text']}; width: 100%;"
                )
            else:
                ui.label("No experiments yet. Create one from the Experiments page.").style(
                    f"color: {COLORS['text_muted']};"
                )

        # System info panel
        with ui.column().style(f"min-width: 280px;"):
            ui.label("System Info").style(
                f"color: {COLORS['text']}; font-size: 18px; font-weight: 600; margin-bottom: 8px;"
            )
            with ui.card().style(CARD_STYLE):
                info = state.config.system_info
                for key, val in info.items():
                    with ui.row().classes("justify-between w-full"):
                        ui.label(key.replace("_", " ").title()).style(
                            f"color: {COLORS['text_muted']}; font-size: 13px;"
                        )
                        ui.label(str(val)).style(
                            f"color: {COLORS['text']}; font-size: 13px; font-weight: 600;"
                        )

            # Triple counts by tool
            counts = state.triples.count_by_tool()
            if counts:
                ui.label("Triples by Tool").style(
                    f"color: {COLORS['text']}; font-size: 16px; font-weight: 600; "
                    "margin-top: 16px; margin-bottom: 8px;"
                )
                with ui.card().style(CARD_STYLE):
                    for tool, cnt in sorted(counts.items()):
                        with ui.row().classes("justify-between w-full"):
                            ui.label(tool).style(
                                f"color: {COLORS['text_muted']}; font-size: 13px;"
                            )
                            ui.label(str(cnt)).style(
                                f"color: {COLORS['primary']}; font-size: 13px; font-weight: 600;"
                            )
