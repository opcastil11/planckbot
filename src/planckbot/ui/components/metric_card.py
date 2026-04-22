"""Reusable metric card component."""

from nicegui import ui
from planckbot.ui.theme import COLORS


def metric_card(title: str, value: str, subtitle: str = "", icon: str = "analytics", color: str = ""):
    """A single metric display card."""
    color = color or COLORS["primary"]
    with ui.card().style(
        f"background-color: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        "border-radius: 12px; padding: 16px; min-width: 200px;"
    ):
        with ui.row().classes("items-center gap-3 no-wrap"):
            ui.icon(icon).style(
                f"color: {color}; font-size: 28px; "
                f"background: {color}22; border-radius: 8px; padding: 8px;"
            )
            with ui.column().classes("gap-0"):
                ui.label(title).style(
                    f"color: {COLORS['text_muted']}; font-size: 12px; "
                    "text-transform: uppercase; letter-spacing: 0.5px;"
                )
                ui.label(value).style(
                    f"color: {COLORS['text']}; font-size: 24px; font-weight: 700;"
                )
                if subtitle:
                    ui.label(subtitle).style(
                        f"color: {COLORS['text_muted']}; font-size: 11px;"
                    )
