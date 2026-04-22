"""Status badge component."""

from nicegui import ui
from planckbot.ui.theme import STATUS_COLORS, COLORS


def status_badge(status: str):
    """Colored status badge."""
    color = STATUS_COLORS.get(status, COLORS["text_muted"])
    ui.label(status).style(
        f"color: {color}; background: {color}22; "
        "padding: 2px 10px; border-radius: 12px; "
        "font-size: 12px; font-weight: 600; text-transform: uppercase;"
    )
