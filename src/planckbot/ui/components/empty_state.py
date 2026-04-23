"""Branded empty state — shown anywhere a list has no content.

Uses the PlanckBots hero mark (dimmed) with a title, hint, and optional
call-to-action. Replaces bare `ui.label("no experiments yet")` across the app.

    empty_state(
        title="No experiments yet",
        hint="Create your first experiment to start training adapters.",
        cta_label="New Experiment",
        cta_action=dialog.open,
    )
"""

from __future__ import annotations

from typing import Callable

from nicegui import ui

from planckbot.ui.theme import (
    COLORS,
    RADIUS_LG,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    TEXT_LG,
    TEXT_MD,
    TEXT_XS,
)


def empty_state(
    title: str,
    hint: str | None = None,
    cta_label: str | None = None,
    cta_action: Callable[[], None] | None = None,
    icon: str | None = None,
):
    """Render a centered empty-state card."""
    with ui.column().classes("w-full items-center justify-center").style(
        f"padding: {SPACE_XL * 2}px {SPACE_LG}px; "
        f"background: radial-gradient(600px 300px at 50% 40%, "
        f"{COLORS['primary']}08 0%, transparent 60%); "
        f"border-radius: {RADIUS_LG}px; "
        f"border: 1px dashed {COLORS['border']};"
    ):
        if icon:
            ui.icon(icon).style(
                f"color: {COLORS['primary']}; font-size: 48px; "
                f"opacity: 0.6; margin-bottom: {SPACE_MD}px;"
            )
        else:
            # Use the brand mark at reduced opacity — it's the most
            # on-brand empty-state "illustration" we have.
            ui.image("/branding/planckbots-mark-192.png").style(
                "width: 96px; height: 96px; border-radius: 50%; "
                "opacity: 0.5; object-fit: cover; "
                f"box-shadow: 0 0 48px {COLORS['primary']}1a; "
                f"margin-bottom: {SPACE_MD}px;"
            )

        ui.label(title).style(
            f"color: {COLORS['text']}; "
            f"font-size: {TEXT_LG}px; font-weight: 600; "
            "letter-spacing: -0.2px; text-align: center;"
        )
        if hint:
            ui.label(hint).style(
                f"color: {COLORS['text_muted']}; "
                f"font-size: {TEXT_MD}px; line-height: 1.55; "
                f"text-align: center; max-width: 440px; "
                f"margin-top: {SPACE_SM}px;"
            )
        if cta_label and cta_action:
            ui.button(cta_label, on_click=cta_action, icon="add").props(
                "color=primary unelevated"
            ).style(f"margin-top: {SPACE_MD}px;")
