"""Big stat card — the number is the hero.

A larger, more confident stat presentation than `metric_card`. Use this
where the value should dominate (dashboard hero, top of key pages).

    stat_card(
        label="Experiments",
        value=47,
        delta="+3 this week",
        icon="science",
        tint="primary",
    )
"""

from __future__ import annotations

from typing import Literal

from nicegui import ui

from planckbot.ui.theme import (
    COLORS,
    DURATION_NORMAL,
    EASE_OUT,
    RADIUS_LG,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    TEXT_SM,
    TEXT_XS,
    label_style,
    number_fmt,
    number_style,
)


Tint = Literal["primary", "accent", "brass", "info", "warning", "error", "muted"]


_TINT_MAP: dict[str, str] = {
    "primary": COLORS["primary"],
    "accent": COLORS["accent"],
    "brass": COLORS["brass"],
    "info": COLORS["info"],
    "warning": COLORS["warning"],
    "error": COLORS["error"],
    "muted": COLORS["text_muted"],
}


def stat_card(
    label: str,
    value: int | float | str,
    delta: str | None = None,
    icon: str | None = None,
    tint: Tint = "primary",
    min_width: int = 200,
):
    """Big-number stat card with optional delta and icon.

    The card subtly lifts on hover and has a colored accent stripe along
    the top tied to `tint`. Numbers are formatted with thousands separators.
    """
    color = _TINT_MAP.get(tint, COLORS["primary"])
    value_str = number_fmt(value) if isinstance(value, (int, float)) else str(value)

    with ui.element("div").classes("planck-stat-card").style(
        f"min-width: {min_width}px; flex: 1; "
        f"background: linear-gradient(180deg, {COLORS['surface']} 0%, "
        f"{COLORS['bg']} 140%); "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px; "
        f"padding: {SPACE_LG}px; position: relative; overflow: hidden; "
        f"transition: all {DURATION_NORMAL} {EASE_OUT};"
    ):
        # Top accent stripe
        ui.element("div").style(
            f"position: absolute; top: 0; left: 0; right: 0; height: 2px; "
            f"background: linear-gradient(90deg, {color} 0%, transparent 100%); "
            f"opacity: 0.8;"
        )

        with ui.row().classes("w-full items-start justify-between no-wrap"):
            ui.label(label).style(label_style())
            if icon:
                ui.icon(icon).style(
                    f"color: {color}; font-size: 18px; opacity: 0.9;"
                )

        ui.label(value_str).style(
            number_style(color=COLORS["text"]) + f" margin-top: {SPACE_MD}px;"
        )

        if delta:
            ui.label(delta).style(
                f"color: {color}; font-size: {TEXT_XS}px; "
                f"font-weight: 600; letter-spacing: 0.3px; "
                f"margin-top: {SPACE_SM}px;"
            )
