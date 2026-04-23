"""Layer D page: synthesized tools + gap reports."""

from __future__ import annotations

from nicegui import ui

from planckbot.ui.components.empty_state import empty_state
from planckbot.ui.components.page_header import page_header
from planckbot.ui.state import get_state
from planckbot.ui.theme import (
    COLORS,
    RADIUS_LG,
    SPACE_LG,
    SPACE_MD,
    TEXT_MD,
    TEXT_SM,
    heading_style,
    label_style,
    relative_time,
)


def _status_color(status: str) -> str:
    return {
        "active": COLORS["success"],
        "draft": COLORS["warning"],
        "retired": COLORS["text_muted"],
        "open": COLORS["primary"],
        "accepted": COLORS["success"],
        "rejected": COLORS["text_muted"],
    }.get(status, COLORS["text_muted"])


def _tools_section(state) -> None:
    ui.label("Synthesized tools").style(heading_style(size=TEXT_MD))
    tools = state.synth.list_all()
    container = ui.column().classes("w-full gap-0").style(
        f"background: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px; overflow: hidden;"
    )
    with container:
        if not tools:
            empty_state(
                title="No synthesized tools yet",
                hint=(
                    "Accept a gap report below or create one with "
                    "`planckbot synth create spec.json`."
                ),
                icon="auto_fix_high",
            )
            return
        # Header
        with ui.row().classes("w-full items-center no-wrap gap-3").style(
            f"padding: {SPACE_MD}px {SPACE_LG}px; "
            f"background: {COLORS['surface2']};"
        ):
            for label, flex in [
                ("Name", "flex: 2;"),
                ("Status", "flex: 0 0 80px;"),
                ("Created", "flex: 0 0 140px;"),
                ("Actions", "flex: 0 0 auto;"),
            ]:
                ui.label(label).style(label_style() + " " + flex)

        for t in tools:
            with ui.row().classes("w-full items-center no-wrap gap-3").style(
                f"padding: {SPACE_MD}px {SPACE_LG}px; "
                f"border-top: 1px solid {COLORS['border']};"
            ):
                with ui.column().classes("gap-0").style("flex: 2; min-width: 0;"):
                    ui.label(t.name).style(
                        f"color: {COLORS['text']}; font-weight: 600; "
                        f"font-size: {TEXT_MD}px;"
                    )
                    ui.label(t.description or "—").style(
                        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                        "overflow: hidden; text-overflow: ellipsis; "
                        "white-space: nowrap;"
                    ).tooltip(t.description or "")

                ui.label(t.status).style(
                    f"color: {_status_color(t.status)}; "
                    f"font-size: {TEXT_SM}px; font-weight: 600; "
                    "flex: 0 0 80px; text-transform: uppercase;"
                )
                ui.label(relative_time(t.created_at)).style(
                    f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                    "flex: 0 0 140px;"
                )

                with ui.row().classes("items-center gap-1").style("flex: 0 0 auto;"):
                    if t.status == "active":
                        def _deact(name=t.name):
                            from planckbot.synth.meta import deactivate_tool
                            try:
                                deactivate_tool(name, conn=state.conn)
                                ui.notify(f"retired {name}", type="warning")
                            except ValueError as e:
                                ui.notify(str(e), type="negative")

                        ui.button(icon="pause", on_click=_deact).props(
                            "flat dense round size=sm"
                        ).tooltip("deactivate")
                    else:
                        def _act(name=t.name):
                            from planckbot.synth.meta import activate_tool
                            try:
                                activate_tool(name, conn=state.conn)
                                ui.notify(f"activated {name}", type="positive")
                            except ValueError as e:
                                ui.notify(str(e), type="negative")

                        ui.button(icon="play_arrow", on_click=_act).props(
                            "flat dense round size=sm color=primary"
                        ).tooltip("activate")


def _gaps_section(state) -> None:
    ui.label("Gap reports").style(
        heading_style(size=TEXT_MD) + f" margin-top: {SPACE_LG}px;"
    )
    ui.label(
        "Tool sequences the detector flagged as candidates for a merged tool."
    ).style(f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px;")

    reports = state.gaps.list_all(limit=20)
    container = ui.column().classes("w-full gap-0").style(
        f"background: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px; overflow: hidden; "
        f"margin-top: 8px;"
    )
    with container:
        if not reports:
            empty_state(
                title="No gap reports yet",
                hint=(
                    "Add a `detect_tool_gaps` cron job — it populates this table "
                    "from the triples you've already recorded."
                ),
                icon="insights",
            )
            return
        with ui.row().classes("w-full items-center no-wrap gap-3").style(
            f"padding: {SPACE_MD}px {SPACE_LG}px; "
            f"background: {COLORS['surface2']};"
        ):
            for label, flex in [
                ("Sequence", "flex: 3;"),
                ("Count", "flex: 0 0 60px;"),
                ("Status", "flex: 0 0 80px;"),
                ("When", "flex: 0 0 120px;"),
                ("", "flex: 0 0 auto;"),
            ]:
                ui.label(label).style(label_style() + " " + flex)

        for r in reports:
            with ui.row().classes("w-full items-center no-wrap gap-3").style(
                f"padding: {SPACE_MD}px {SPACE_LG}px; "
                f"border-top: 1px solid {COLORS['border']};"
            ):
                ui.label(" → ".join(r.tool_sequence)).style(
                    f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
                    f"font-family: monospace; flex: 3;"
                )
                ui.label(str(r.occurrences)).style(
                    f"color: {COLORS['accent']}; font-weight: 700; "
                    f"font-size: {TEXT_MD}px; "
                    "flex: 0 0 60px; font-variant-numeric: tabular-nums;"
                )
                ui.label(r.status).style(
                    f"color: {_status_color(r.status)}; "
                    f"font-size: {TEXT_SM}px; font-weight: 600; "
                    "flex: 0 0 80px; text-transform: uppercase;"
                )
                ui.label(relative_time(r.created_at)).style(
                    f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                    "flex: 0 0 120px;"
                )
                with ui.row().classes("items-center gap-1").style("flex: 0 0 auto;"):
                    if r.status == "open":
                        def _accept(rid=r.id):
                            state.gaps.set_status(rid, "accepted")
                            ui.notify("accepted — ready to synthesize",
                                      type="positive")

                        def _reject(rid=r.id):
                            state.gaps.set_status(rid, "rejected")
                            ui.notify("rejected", type="warning")

                        ui.button(icon="check", on_click=_accept).props(
                            "flat dense round size=sm color=positive"
                        ).tooltip("accept (mark for synthesis)")
                        ui.button(icon="close", on_click=_reject).props(
                            "flat dense round size=sm color=negative"
                        ).tooltip("reject")


def synth_page():
    state = get_state()

    page_header(
        title="Tool synthesis (Layer D)",
        subtitle=(
            "Detect repeated tool sequences, turn them into candidates for new "
            "merged tools, and ship the accepted ones through the planckbot-synth "
            "MCP server."
        ),
    )
    _tools_section(state)
    _gaps_section(state)
