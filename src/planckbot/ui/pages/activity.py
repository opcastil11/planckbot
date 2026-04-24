"""Activity page — live feed of everything PlanckBot is doing.

SQLite-backed event bus: the proxy, cron daemon, synth, and training all
INSERT into `activity_events`. This page polls `id > last_seen_id` every
two seconds and appends new rows to a scrollable log. No websocket
infra — NiceGUI already has its own, so rendering updates reach the
browser in the same tick the data is fetched.

Because this page has filter state (the source dropdown), it must NOT
be wrapped in `live_seconds=...` at the route level — the rebuild would
wipe the dropdown. Instead we attach a `ui.timer` to just the log
section and keep the filter controls outside the rebuild radius.
"""

from __future__ import annotations

from datetime import datetime, timezone

from nicegui import ui

from planckbot import activity
from planckbot.ui.components import page_header
from planckbot.ui.state import get_state
from planckbot.ui.theme import (
    COLORS,
    RADIUS_LG,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    TEXT_MD,
    TEXT_SM,
    label_style,
)


# Source → (emoji, accent color). Accent is taken from the existing
# palette when there's a natural match, or borrowed from a nearby hue.
SOURCE_ICONS: dict[str, tuple[str, str]] = {
    "proxy":    ("📡", COLORS["primary"]),
    "cron":     ("⏱️", COLORS["accent"]),
    "synth":    ("🧪", COLORS.get("warning", "#d4a017")),
    "training": ("🎓", COLORS.get("info", "#5aa8e0")),
    "ui":       ("🖥️", COLORS["text_muted"]),
}

# Per-source kind overrides for block/intervene so users can spot them
# even when scrolling fast. These show next to the source chip.
KIND_ICONS: dict[str, str] = {
    "block":      "⛔",
    "redact":     "✂️",
    "intervene":  "⚡",
    "job_error":  "❌",
    "unbless":    "⚠️",
}

# Max rows to render in the DOM. Anything older gets trimmed client-side
# when we hit the cap. The underlying DB keeps the full history (subject
# to activity.trim_events rotation).
MAX_RENDERED = 400


def activity_page():
    state = get_state()

    # Mark everything up to now as "seen" so the sidebar badge clears.
    # The poller below will update this value as new events stream in,
    # so leaving the page and coming back doesn't re-flag what you
    # already watched scroll past.
    _mark_seen(state)

    page_header(
        "Activity",
        "Live feed of proxy interceptions, cron runs, synth lifecycle, "
        "and training events. Updates every 2 seconds.",
    )

    # --- filter row (state-preserving, outside the poller) ---------------
    controls = ui.row().classes("w-full items-center gap-3").style(
        f"margin-bottom: {SPACE_MD}px;"
    )
    with controls:
        ui.label("Source:").style(label_style())
        source_select = ui.select(
            options={
                "all": "All",
                "proxy": "📡 Proxy",
                "cron": "⏱️ Cron",
                "synth": "🧪 Synth",
                "training": "🎓 Training",
            },
            value="all",
        ).props("dense options-dense outlined").style("min-width: 160px;")

        project_options = {"all": "All projects"}
        for p in state.projects.list_all():
            project_options[p.id] = p.name
        project_select = ui.select(
            options=project_options, value="all",
        ).props("dense options-dense outlined").style("min-width: 180px;")

        paused = {"value": False}

        def _toggle_pause():
            paused["value"] = not paused["value"]
            pause_btn.text = "Resume" if paused["value"] else "Pause"

        pause_btn = ui.button("Pause", on_click=_toggle_pause).props(
            "flat dense"
        )

        def _clear():
            log_column.clear()
            last_id["value"] = _latest_id(state)
            _seed(state, log_column, source_select.value,
                  project_select.value)

        ui.button("Clear", on_click=_clear).props("flat dense")

    # --- scrolling event log --------------------------------------------
    log_wrapper = ui.column().classes("w-full").style(
        f"background: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px; "
        f"padding: {SPACE_SM}px; "
        "height: 600px; overflow-y: auto; "
        "font-family: ui-monospace, SFMono-Regular, Menlo, monospace; "
    )

    log_column = ui.column().classes("w-full gap-0")
    log_wrapper.default_slot.children.append(log_column)  # type: ignore[attr-defined]

    last_id = {"value": 0}

    def _filtered_source() -> str | None:
        v = source_select.value
        return None if v in (None, "all") else v

    def _filtered_project() -> str | None:
        v = project_select.value
        return None if v in (None, "all") else v

    # Seed with the tail of recent events so the user sees context.
    _seed(state, log_column, source_select.value, project_select.value)
    last_id["value"] = _latest_id(state)

    # On filter change, rebuild the seed from scratch.
    def _on_filter_change():
        log_column.clear()
        _seed(state, log_column, source_select.value, project_select.value)
        last_id["value"] = _latest_id(state)

    source_select.on("update:model-value", lambda _: _on_filter_change())
    project_select.on("update:model-value", lambda _: _on_filter_change())

    def _poll():
        if paused["value"]:
            return
        new_events = activity.list_events(
            state.conn,
            since_id=last_id["value"],
            source=_filtered_source(),
            project_id=_filtered_project(),
            limit=100,
        )
        if not new_events:
            return
        with log_column:
            for ev in new_events:
                _render_event_row(ev)
        last_id["value"] = new_events[-1].id
        # Keep the sidebar badge in sync with what's on screen; as long
        # as the user is parked on /activity, nothing is "unread".
        _mark_seen(state, up_to=last_id["value"])
        _trim_dom(log_column)

    # 500 ms poll: at this cadence the feed feels push-based without
    # actually moving to websocket+pub/sub infra. Still an indexed
    # `WHERE id > ?` lookup — microseconds of DB work per tick.
    ui.timer(0.5, _poll)


def _mark_seen(state, *, up_to: int | None = None) -> None:
    """Advance `last_seen_activity_id` to `up_to` (or MAX(id) if omitted)."""
    if up_to is None:
        row = state.conn.execute(
            "SELECT MAX(id) FROM activity_events"
        ).fetchone()
        up_to = int(row[0]) if row and row[0] else 0
    if up_to > state.last_seen_activity_id:
        state.last_seen_activity_id = up_to


def _latest_id(state) -> int:
    row = state.conn.execute(
        "SELECT MAX(id) FROM activity_events"
    ).fetchone()
    return int(row[0]) if row and row[0] else 0


def _seed(state, log_column, source_filter, project_filter) -> None:
    src = None if source_filter in (None, "all") else source_filter
    proj = None if project_filter in (None, "all") else project_filter
    events = activity.recent_events(
        state.conn, limit=150, source=src, project_id=proj,
    )
    with log_column:
        if not events:
            ui.label(
                "No activity yet — trigger a tool call, cron run, or "
                "synth activation to see events here."
            ).style(
                f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                f"padding: {SPACE_MD}px;"
            )
            return
        for ev in events:
            _render_event_row(ev)


def _render_event_row(ev) -> None:
    emoji, color = SOURCE_ICONS.get(ev.source, ("•", COLORS["text"]))
    kind_icon = KIND_ICONS.get(ev.kind, "")
    ts_local = ev.ts.astimezone().strftime("%H:%M:%S")

    with ui.row().classes("w-full items-start no-wrap").style(
        f"padding: 4px 8px; gap: 8px; "
        f"border-bottom: 1px solid {COLORS['border']}33;"
    ):
        ui.label(ts_local).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "font-variant-numeric: tabular-nums; "
            "flex: 0 0 72px; padding-top: 1px;"
        )
        ui.label(f"{emoji} {ev.source}").style(
            f"color: {color}; font-size: {TEXT_SM}px; "
            "font-weight: 600; flex: 0 0 110px;"
        )
        ui.label(f"{kind_icon} {ev.kind}".strip()).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "flex: 0 0 100px;"
        )
        ui.label(ev.message).style(
            f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
            "flex: 1; word-break: break-word;"
        )


def _trim_dom(log_column) -> None:
    children = getattr(log_column.default_slot, "children", [])
    extra = len(children) - MAX_RENDERED
    if extra > 0:
        for child in children[:extra]:
            child.delete()
