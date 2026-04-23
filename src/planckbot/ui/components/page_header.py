"""Consistent page header: title + subtitle on the left, optional actions on the right.

Every page in the workbench should open with `page_header(title, subtitle)`
so the visual hierarchy and spacing stay identical across the app.

    page_header(
        "Experiments",
        "Run, compare, and promote your Planck models.",
        actions=lambda: ui.button("New Experiment", icon="add"),
    )
"""

from __future__ import annotations

from typing import Callable

from nicegui import ui

from planckbot.ui.theme import (
    COLORS,
    SPACE_MD,
    TEXT_XL,
    heading_style,
    subtitle_style,
)


def page_header(
    title: str,
    subtitle: str | None = None,
    actions: Callable[[], None] | None = None,
    eyebrow: str | None = None,
):
    """Render a standard page header.

    Args:
        title:    the main page title.
        subtitle: a short description that sits under the title.
        actions:  optional callable that renders buttons/controls on the right.
        eyebrow:  optional tiny label above the title (e.g. a section).
    """
    with ui.row().classes("w-full items-start justify-between no-wrap").style(
        f"margin-bottom: {SPACE_MD}px;"
    ):
        with ui.column().classes("gap-1"):
            if eyebrow:
                ui.label(eyebrow).style(
                    f"color: {COLORS['primary']}; "
                    "font-size: 11px; font-weight: 700; "
                    "letter-spacing: 1.5px; text-transform: uppercase;"
                )
            ui.label(title).style(heading_style(size=TEXT_XL))
            if subtitle:
                ui.label(subtitle).style(subtitle_style())
        if actions:
            with ui.row().classes("items-center gap-2"):
                actions()

    # Thin separator rule that matches the dark palette
    ui.element("div").style(
        f"width: 100%; height: 1px; "
        f"background: linear-gradient(90deg, {COLORS['border_glow']}44 0%, "
        f"{COLORS['border']}22 100%); "
        f"margin: 0 0 {SPACE_MD}px 0;"
    )
