"""Cron jobs page: list, create, toggle, run-now, delete."""

from __future__ import annotations

import json

from nicegui import ui

from planckbot.cron.daemon import (
    Daemon, daemon_status, systemd_action, systemd_status,
)
from planckbot.db.models import CronJob
from planckbot.ui.components.empty_state import empty_state
from planckbot.ui.components.page_header import page_header
from planckbot.ui.state import get_state
from planckbot.ui.theme import (
    CARD_STYLE,
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


def _status_color(status: str | None) -> str:
    if status == "ok":
        return COLORS["success"]
    if status == "error":
        return COLORS["error"]
    return COLORS["text_muted"]


def _job_row(state, job: CronJob) -> None:
    with ui.row().classes(
        "w-full items-center no-wrap gap-3"
    ).style(
        f"padding: {SPACE_MD}px {SPACE_LG}px; "
        f"border-top: 1px solid {COLORS['border']};"
    ):
        # Name + type
        with ui.column().classes("gap-0").style("flex: 2; min-width: 0;"):
            ui.label(job.name).style(
                f"color: {COLORS['text']}; font-weight: 600; "
                f"font-size: {TEXT_MD}px; "
                "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            )
            ui.label(job.job_type).style(
                f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                "font-family: monospace;"
            )

        # Interval
        ui.label(f"{job.interval_seconds}s").style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "flex: 0 0 70px; font-variant-numeric: tabular-nums;"
        )

        # Last run
        last_run_text = relative_time(job.last_run_at) if job.last_run_at else "never"
        ui.label(last_run_text).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "flex: 0 0 120px;"
        )

        # Status badge
        status = job.last_status or "—"
        ui.label(status).style(
            f"color: {_status_color(job.last_status)}; "
            f"font-size: {TEXT_SM}px; font-weight: 600; "
            "flex: 0 0 64px; text-transform: uppercase;"
        )

        # Next run
        nxt = relative_time(job.next_run_at) if job.next_run_at else "—"
        ui.label(nxt).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "flex: 0 0 120px;"
        )

        # Actions
        with ui.row().classes("items-center gap-1").style("flex: 0 0 auto;"):
            ui.switch(
                value=bool(job.enabled),
                on_change=lambda e, jid=job.id: (
                    state.cron.set_enabled(jid, e.value)
                ),
            ).tooltip("enabled")

            def _run_now(jid=job.id):
                daemon = Daemon(state.conn, registry=state.cron_registry)
                status, output = daemon.run_job(jid)
                ui.notify(
                    f"{status}: {output[:160]}",
                    type="positive" if status == "ok" else "negative",
                    multi_line=True,
                )

            ui.button(icon="play_arrow", on_click=_run_now).props(
                "flat dense round size=sm"
            ).tooltip("run now")

            def _delete(jid=job.id, name=job.name):
                state.cron.delete(jid)
                ui.notify(f"deleted {name}", type="warning")

            ui.button(icon="delete_outline", on_click=_delete).props(
                "flat dense round size=sm color=negative"
            ).tooltip("delete")

    # Show last output (collapsible) underneath if there is one
    if job.last_output:
        out = job.last_output
        preview = out if len(out) <= 160 else out[:160] + "…"
        ui.label(preview).style(
            f"color: {_status_color(job.last_status)}; "
            f"font-size: {TEXT_SM}px; "
            f"padding: 0 {SPACE_LG}px {SPACE_MD}px; "
            "font-family: monospace; white-space: pre-wrap;"
        ).tooltip(out)


# Per-job-type hint shown under the form. Lives in module scope so tests
# and future job types can see the canonical reference in one place.
_JOB_TYPE_HINTS: dict[str, str] = {
    "noop": (
        "params: { \"message\": \"…\" } — writes the message to last_output. "
        "Useful as a heartbeat or to verify the daemon is firing."
    ),
    "autolabel": (
        "params: { \"tool\": \"...\", \"reference_path\": \"/path/to/ref.txt\", "
        "\"recent\": 50 }. Back-fills filtered_output on the N most recent "
        "unlabeled triples for `tool` using a single reference text "
        "(batch-style — may produce false positives, see autolabel_precise)."
    ),
    "autolabel_precise": (
        "params: { \"tool\": \"...\", \"recent\": 50 }. Per-triple labeling — "
        "for each unlabeled triple, finds the exact follow-up Claude message "
        "in the JSONL transcript and labels using only that text. Recommended."
    ),
    "retrain": (
        "params: { \"tool\": \"...\", \"fixture_path\": \"data/fixtures/X.json\", "
        "\"min_new_labeled\": 16 }. Signals readiness to retrain — prints the "
        "command. Does not actually train (would block the daemon ~10 min)."
    ),
    "conversation_scanner": (
        "params: { \"output_path\": \"/tmp/ref.txt\", "
        "\"project_slug\": \"-home-kai-...\" (optional), "
        "\"max_messages\": 30, \"lookback_hours\": 24 }. "
        "Reads ~/.claude/projects/<slug>/*.jsonl, dumps recent assistant "
        "text blocks to `output_path`. Pair with an autolabel job pointed "
        "at the same path to close the auto-label loop."
    ),
    "detect_tool_gaps": (
        "params: { \"window_seconds\": 30, \"min_occurrences\": 2, "
        "\"ngram_min\": 2, \"ngram_max\": 3 }. Scans proxy triples for "
        "repeating tool sequences within the time window and writes them "
        "to gap_reports for Layer-D synthesis."
    ),
    "claude_code_ingest": (
        "params: { \"project_path\": \"/abs/path\" (optional — defaults to "
        "the job's project), \"limit\": 5000, \"skip_errors\": true, "
        "\"since\": \"2026-05-09T00:00:00\" (optional) }. Pulls every "
        "tool_use+tool_result pair from ~/.claude/projects/<slug>/*.jsonl "
        "into the triples table. Idempotent — safe to re-run frequently."
    ),
}


def _hint_for(job_type: str) -> str:
    return _JOB_TYPE_HINTS.get(
        job_type,
        f"(no inline reference for `{job_type}` — see "
        f"src/planckbot/cron/jobs.py for params)",
    )


def _create_form(state, on_created) -> None:
    with ui.card().style(CARD_STYLE + " width: 100%; margin-top: 16px;"):
        ui.label("Create job").style(heading_style(size=TEXT_MD))
        name = ui.input("Name").style("width: 280px;")
        types = state.cron_registry.types()
        job_type = ui.select(
            types,
            value=types[0],
            label="Type",
        ).style("width: 240px;")
        interval = ui.number(
            "Interval (seconds)", value=300, min=10, step=10,
        ).style("width: 200px;")
        params_area = ui.textarea(
            "Params (JSON)", value="{}",
        ).style(
            "width: 100%; font-family: monospace; min-height: 90px;"
        )
        # Dynamic hint: updates whenever the user picks a different job type.
        hint = ui.label(_hint_for(job_type.value)).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "white-space: normal; line-height: 1.45;"
        )
        job_type.on_value_change(
            lambda e: setattr(hint, "text", _hint_for(e.value))
        )

        def submit():
            if not name.value or not name.value.strip():
                ui.notify("name required", type="negative")
                return
            if state.cron.by_name(name.value.strip()):
                ui.notify("a job with that name already exists",
                          type="negative")
                return
            try:
                params = json.loads(params_area.value or "{}")
            except json.JSONDecodeError as e:
                ui.notify(f"invalid JSON: {e}", type="negative")
                return
            # New jobs inherit whichever project is the UI lens right now.
            # Keeping the job global is a CLI-only affair for simplicity.
            job = CronJob(
                name=name.value.strip(),
                job_type=job_type.value,
                params=params,
                interval_seconds=int(interval.value),
                project_id=state.active_project_id(),
            )
            state.cron.add(job)
            ui.notify(f"created {job.name}", type="positive")
            on_created()

        ui.button("Create", on_click=submit, icon="add").props("color=primary")


def _daemon_pill_render(container, status: dict, sysd: dict) -> None:
    """Re-render the daemon health pill + control buttons into `container`.

    Buttons are only shown when the systemd user-unit is installed
    (otherwise we'd be lying about being able to do anything). Users
    without systemd see the pill plus the manual command hint.
    """
    container.clear()
    running = status.get("running", False)
    pid = status.get("pid")
    color = COLORS["success"] if running else COLORS["text_muted"]
    bg = COLORS["surface2"]
    label = "DAEMON: running" if running else "DAEMON: stopped"
    if running and pid:
        detail = f"pid {pid}"
    elif sysd.get("available"):
        detail = "use the buttons →" if not running else ""
    else:
        detail = "start with `planckbot cron daemon` or `planckbot systemd install`"

    def _do(action: str):
        ok, msg = systemd_action(action)
        ui.notify(
            f"{action}: {msg}",
            type="positive" if ok else "negative",
            timeout=2500,
        )

    with container:
        with ui.row().classes("items-center gap-3 no-wrap").style(
            "width: fit-content;"
        ):
            with ui.row().classes("items-center gap-2 no-wrap").style(
                f"background: {bg}; "
                f"border: 1px solid {color}55; "
                f"padding: 4px 12px; border-radius: 999px;"
            ):
                ui.element("div").style(
                    f"width: 8px; height: 8px; border-radius: 50%; "
                    f"background: {color}; "
                    + ("box-shadow: 0 0 8px " + color + ";" if running else "")
                )
                ui.label(label).style(
                    f"color: {color}; font-size: {TEXT_SM}px; font-weight: 600; "
                    "letter-spacing: 0.4px;"
                )
                if detail:
                    ui.label(detail).style(
                        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                        "font-family: monospace;"
                    )

            # systemd controls
            if sysd.get("available"):
                if running:
                    ui.button(
                        icon="stop", on_click=lambda: _do("stop"),
                    ).props("flat dense round size=sm color=negative").tooltip(
                        "systemctl --user stop planckbot-cron.service"
                    )
                    ui.button(
                        icon="restart_alt", on_click=lambda: _do("restart"),
                    ).props("flat dense round size=sm").tooltip(
                        "systemctl --user restart planckbot-cron.service"
                    )
                else:
                    ui.button(
                        icon="play_arrow", on_click=lambda: _do("start"),
                    ).props("flat dense round size=sm color=positive").tooltip(
                        "systemctl --user start planckbot-cron.service"
                    )
                # Boot-up indicator
                if sysd.get("enabled"):
                    ui.label("auto-start ✓").style(
                        f"color: {COLORS['text_muted']}; "
                        f"font-size: {TEXT_SM}px;"
                    )


def cron_page():
    state = get_state()

    page_header(
        title="Cron",
        subtitle=(
            "Scheduled background jobs — auto-label unlabeled triples, "
            "retrain adapters when new supervision accumulates. Run the "
            "daemon with `.venv/bin/planckbot cron daemon`."
        ),
    )

    daemon_pill = ui.row().classes("w-full").style(
        f"margin-top: {SPACE_MD}px;"
    )
    _daemon_pill_render(daemon_pill, daemon_status(), systemd_status())

    jobs_container = ui.column().classes("w-full gap-0").style(
        f"background: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px; overflow: hidden; margin-top: 12px;"
    )

    def refresh():
        _daemon_pill_render(daemon_pill, daemon_status(), systemd_status())
        jobs_container.clear()
        jobs = state.cron.list_all(project_id=state.active_project_id())
        with jobs_container:
            if not jobs:
                empty_state(
                    title="No jobs yet",
                    hint="Use the form below to schedule your first job.",
                    icon="schedule",
                )
                return

            # Header row
            with ui.row().classes(
                "w-full items-center no-wrap gap-3"
            ).style(
                f"padding: {SPACE_MD}px {SPACE_LG}px; "
                f"background: {COLORS['surface2']};"
            ):
                for label, flex in [
                    ("Job", "flex: 2;"),
                    ("Interval", "flex: 0 0 70px;"),
                    ("Last run", "flex: 0 0 120px;"),
                    ("Status", "flex: 0 0 64px;"),
                    ("Next run", "flex: 0 0 120px;"),
                    ("Actions", "flex: 0 0 auto;"),
                ]:
                    ui.label(label).style(label_style() + " " + flex)

            for j in jobs:
                _job_row(state, j)

    refresh()
    _create_form(state, on_created=refresh)
    ui.timer(3.0, refresh)
