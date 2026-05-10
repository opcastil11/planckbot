"""Projects page: create, list, switch, delete target folders.

A project pins a name to an absolute filesystem path. The active project
is the one whose `path` is baked into the `planckbot-fs` MCP entry in
`~/.claude.json`; when the user switches projects here, we rewrite that
file so Claude Code hits the new folder after a restart.

Nothing on this page persists training data or adapters — those rows
already live in their own tables with a `project_id` foreign key; this
page just creates the keys.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from planckbot.ui.components import empty_state, page_header
from planckbot.ui.state import get_state
from planckbot.ui.theme import (
    CARD_STYLE,
    COLORS,
    RADIUS_LG,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    TEXT_LG,
    TEXT_MD,
    TEXT_SM,
    heading_style,
    label_style,
    relative_time,
)


def _rewrite_claude_json(path: str) -> tuple[bool, str]:
    """Thin wrapper around the CLI helper so the UI and CLI share the
    logic for poking ~/.claude.json."""
    from planckbot.cli import _rewrite_mcp_upstream_path
    return _rewrite_mcp_upstream_path(path)


# Stable, project-scoped name for the auto-ingest cron job. Using the first
# 8 chars of the project UUID keeps it stable across renames and makes the
# row easy to spot on /cron.
def _auto_ingest_job_name(project) -> str:
    return f"auto-ingest:{project.id[:8]}"


def _ingest_now(state, project) -> tuple[bool, str]:
    """One-shot: pull every Claude Code tool_use+tool_result pair from
    `~/.claude/projects/<slug>/*.jsonl` into triples for this project.

    Returns (ok, message). Idempotent — dedups by upstream tool_use_id."""
    from planckbot.ingest.claude_code import ClaudeCodeJsonlSource
    try:
        src = ClaudeCodeJsonlSource(project_path=project.path)
        n = src.ingest(state.triples, project_id=project.id)
        return True, f"ingested {n} triple(s) from Claude Code transcripts"
    except Exception as e:
        return False, f"ingest failed: {type(e).__name__}: {e}"


def projects_page():
    state = get_state()
    page_header(
        "Projects",
        "Each project pins a name to a target folder. "
        "Switch active to rewrite ~/.claude.json so Claude Code "
        "watches that folder on next restart.",
    )

    _create_form(state)

    projects = state.projects.list_all()
    if not projects:
        empty_state(
            title="No projects yet",
            hint=(
                "Create your first project above. The folder you point at "
                "will be what the PlanckBots watch."
            ),
        )
        return

    _projects_table(state, projects)


def _create_form(state) -> None:
    with ui.card().style(CARD_STYLE + f" margin-bottom: {SPACE_LG}px;"):
        ui.label("Create project").style(
            heading_style(size=TEXT_LG) + f" margin-bottom: {SPACE_SM}px;"
        )
        with ui.row().classes("w-full items-end no-wrap gap-2"):
            name_in = ui.input(
                label="Name", placeholder="my-repo",
            ).style("flex: 1 1 200px; min-width: 160px;")
            path_in = ui.input(
                label="Absolute path",
                placeholder="/home/kai/Escritorio/PROGRAMACION/planckbot",
            ).style("flex: 2 1 300px; min-width: 260px;")

            def _pick_folder():
                # Server-side folder picker: the workbench runs locally
                # (same machine as the user's filesystem) so we can browse
                # directly instead of relying on the browser's <input type=
                # "file" webkitdirectory> which only yields file uploads.
                _open_folder_picker(
                    start=path_in.value or str(Path.home()),
                    on_pick=lambda p: setattr(path_in, "value", p),
                )

            ui.button(
                "Browse", on_click=_pick_folder, icon="folder_open",
            ).props("flat").style(
                f"color: {COLORS['primary']}; flex: 0 0 auto;"
            )
        with ui.row().classes("w-full items-center no-wrap gap-2").style(
            f"margin-top: {SPACE_SM}px;"
        ):
            desc_in = ui.input(
                label="Description (optional)",
            ).style("flex: 1 1 300px;")
            activate_cb = ui.checkbox("Activate + rewrite claude.json")
            adopt_cb = ui.checkbox(
                "Adopt legacy rows",
            ).tooltip(
                "Reassign every pre-v6 row (NULL project_id) to this project."
            )

            def _submit():
                name = (name_in.value or "").strip()
                path = (path_in.value or "").strip()
                if not name or not path:
                    ui.notify("name and path are required", type="negative")
                    return
                resolved = Path(path).expanduser()
                if not resolved.is_absolute():
                    ui.notify(
                        f"path must be absolute, got {path!r}",
                        type="negative",
                    )
                    return
                try:
                    project = state.projects.create(
                        name=name,
                        path=str(resolved),
                        description=(desc_in.value or "").strip() or None,
                        activate=activate_cb.value,
                    )
                except ValueError as e:
                    ui.notify(str(e), type="negative")
                    return

                if adopt_cb.value:
                    touched = state.projects.adopt_legacy(project.id)
                    n = sum(touched.values())
                    if n:
                        ui.notify(
                            f"adopted {n} legacy row(s) into {project.name}",
                            type="positive",
                        )
                if activate_cb.value:
                    ok, msg = _rewrite_claude_json(str(resolved))
                    ui.notify(
                        f"activated — {msg}",
                        type="positive" if ok else "warning",
                    )
                ui.notify(
                    f"created project {project.name}", type="positive",
                )
                name_in.value = ""
                path_in.value = ""
                desc_in.value = ""
                activate_cb.value = False
                adopt_cb.value = False
                ui.navigate.to("/projects")

            ui.button("Create", on_click=_submit, icon="add").props(
                "color=primary unelevated"
            )


def _projects_table(state, projects) -> None:
    with ui.card().style(CARD_STYLE + " padding: 0;"):
        # Header row. Each column carries an explicit text-align that the
        # content row mirrors via justify-content, so widget chrome (button
        # padding, switch knob) doesn't visually drift off the header.
        with ui.row().classes("w-full items-center no-wrap").style(
            f"padding: {SPACE_MD}px {SPACE_LG}px; "
            f"background: {COLORS['surface2']}; "
            f"border-bottom: 1px solid {COLORS['border']};"
        ):
            for label, flex, align in [
                ("", "flex: 0 0 20px", "left"),
                ("NAME", "flex: 2; min-width: 140px", "left"),
                ("PATH", "flex: 3; min-width: 240px", "left"),
                ("TRIPLES", "flex: 0 0 80px", "right"),
                ("CKPTS", "flex: 0 0 60px", "right"),
                ("ROUTING", "flex: 0 0 160px", "left"),
                ("LAST USED", "flex: 0 0 100px", "left"),
                ("INGEST", "flex: 0 0 100px", "center"),
                ("ACTIONS", "flex: 0 0 180px", "right"),
            ]:
                ui.label(label).style(
                    label_style() + f" {flex}; text-align: {align};"
                )

        for p in projects:
            _project_row(state, p)


def _project_row(state, project) -> None:
    triples = state.triples.count_total(project_id=project.id)
    ckpts = state.checkpoints.count(project_id=project.id)
    last_used = (
        relative_time(project.last_used_at)
        if project.last_used_at else "—"
    )

    with ui.row().classes("w-full items-center no-wrap").style(
        f"padding: {SPACE_MD}px {SPACE_LG}px; "
        f"border-top: 1px solid {COLORS['border']};"
    ):
        # Active marker
        if project.is_active:
            ui.label("★").style(
                f"color: {COLORS['primary']}; font-size: {TEXT_LG}px; "
                "flex: 0 0 20px;"
            ).tooltip("Active project")
        else:
            ui.label("").style("flex: 0 0 20px;")

        # Name + description
        with ui.column().classes("gap-0").style(
            "flex: 2; min-width: 140px; overflow: hidden;"
        ):
            ui.label(project.name).style(
                f"color: {COLORS['text']}; font-weight: 600; "
                f"font-size: {TEXT_MD}px;"
            )
            if project.description:
                ui.label(project.description).style(
                    f"color: {COLORS['text_muted']}; "
                    f"font-size: {TEXT_SM}px;"
                )

        # Path
        ui.label(project.path).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "font-family: monospace; flex: 3; min-width: 240px; "
            "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
        ).tooltip(project.path)

        # Stats — right-aligned so the digit columns line up cleanly.
        ui.label(f"{triples}").style(
            f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
            "font-variant-numeric: tabular-nums; "
            "flex: 0 0 80px; text-align: right;"
        )
        ui.label(f"{ckpts}").style(
            f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
            "font-variant-numeric: tabular-nums; "
            "flex: 0 0 60px; text-align: right;"
        )
        _routing_control(project)
        ui.label(last_used).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "flex: 0 0 100px;"
        )

        _ingest_controls(state, project)

        # Actions — right-justified so the rightmost button sits flush with
        # the table edge regardless of how many actions this row gets.
        with ui.row().classes(
            "items-center gap-1 no-wrap justify-end"
        ).style(
            "flex: 0 0 180px;"
        ):
            if not project.is_active:
                def _switch(pid=project.id, path=project.path):
                    state.projects.set_active(pid)
                    ok, msg = _rewrite_claude_json(path)
                    ui.notify(
                        f"switched → {msg}",
                        type="positive" if ok else "warning",
                    )
                    ui.navigate.to("/projects")

                ui.button(
                    "Switch", on_click=_switch, icon="swap_horiz",
                ).props("flat dense").style(
                    f"color: {COLORS['primary']};"
                )

            def _delete(
                pid=project.id, name=project.name, is_active=project.is_active
            ):
                if is_active:
                    ui.notify(
                        "active project — switch to another first",
                        type="warning",
                    )
                    return
                _confirm_delete_dialog(state, pid, name)

            ui.button(
                "Delete", on_click=_delete, icon="delete",
            ).props("flat dense").style(
                f"color: {COLORS['error']};"
            )


def _ingest_controls(state, project) -> None:
    """Per-project ingest controls — backfill now + auto-ingest toggle.

    The toggle persists as a real cron job (`claude_code_ingest`, scoped to
    this project) named `auto-ingest:<id>`. Existence of that row is what
    decides whether the toggle reads "on". The user can still see + edit the
    job from /cron — this is just the convenience entry-point.
    """
    from planckbot.db.models import CronJob

    job_name = _auto_ingest_job_name(project)
    existing_job = state.cron.by_name(job_name)

    with ui.row().classes(
        "items-center gap-2 no-wrap justify-center"
    ).style(
        "flex: 0 0 100px;"
    ):
        # Backfill button — runs the ingest synchronously and notifies the
        # count. 1k triples in <1s on real hardware, fine for a sync click.
        def _run_ingest(p=project):
            ui.notify("ingesting Claude Code transcripts…", type="info",
                      timeout=1500)
            ok, msg = _ingest_now(state, p)
            ui.notify(msg, type="positive" if ok else "negative",
                      multi_line=True)
            ui.navigate.to("/projects")

        ui.button(
            icon="cloud_download", on_click=_run_ingest,
        ).props("flat dense round size=sm").tooltip(
            "Backfill triples from Claude Code transcripts now "
            "(idempotent — safe to click repeatedly)"
        ).style(f"color: {COLORS['primary']};")

        # Auto-ingest toggle — creating/deleting a cron job is the underlying
        # storage. Default interval 300s mirrors what the docs suggest.
        def _toggle_auto(e, p=project, name=job_name):
            if e.value:
                if state.cron.by_name(name):
                    return  # already on, nothing to do
                state.cron.add(CronJob(
                    name=name,
                    job_type="claude_code_ingest",
                    params={},
                    interval_seconds=300,
                    project_id=p.id,
                ))
                ui.notify(
                    f"auto-ingest on for {p.name} (every 5 min — "
                    "see /cron to tune)",
                    type="positive",
                )
            else:
                row = state.cron.by_name(name)
                if row is None:
                    return
                state.cron.delete(row.id)
                ui.notify(f"auto-ingest off for {p.name}", type="warning")
            ui.navigate.to("/projects")

        ui.switch(
            value=existing_job is not None,
            on_change=_toggle_auto,
        ).props("dense").tooltip(
            "Auto-ingest every 5 min — creates a `claude_code_ingest` "
            "cron job scoped to this project. Requires the cron daemon "
            "to be running."
        )


def _routing_control(project) -> None:
    """Per-row mode selector. Writes to the project folder's CLAUDE.md /
    .claude/settings.json based on the chosen mode and reports back."""
    from planckbot.tools import routing

    project_path = Path(project.path)
    try:
        current = routing.get_mode(project_path)
    except Exception:
        # Missing folder, unreadable file, etc. — surface as "—" and skip.
        ui.label("—").style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "flex: 0 0 160px;"
        ).tooltip(f"could not read routing state for {project_path}")
        return

    labels = {
        "off": "Off",
        "soft": "Soft (CLAUDE.md)",
        "hard": "Hard (deny native)",
    }

    def _apply(e):
        new_mode = e.value
        if new_mode == current:
            return
        try:
            state = routing.set_mode(project_path, new_mode)
        except ValueError as err:
            ui.notify(f"{err}", type="negative")
            return
        hint = (
            "restart Claude Code in that folder for changes to take effect"
            if state.mode != "off"
            else "restored"
        )
        ui.notify(
            f"{project.name} routing → {state.mode} ({hint})",
            type="positive",
        )
        ui.navigate.to("/projects")

    ui.select(
        options=labels,
        value=current,
        on_change=_apply,
    ).props("dense options-dense borderless").style(
        "flex: 0 0 160px; font-size: 13px;"
    ).tooltip(
        "Off: no managed files. "
        "Soft: nudges Claude via CLAUDE.md block. "
        "Hard: also denies native Read/Glob/Grep."
    )


def _confirm_delete_dialog(state, project_id: str, name: str) -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label(f"Delete project '{name}'?").style(
            heading_style(size=TEXT_LG)
        )
        purge_cb = ui.checkbox(
            "Also purge triples, checkpoints, cron jobs, synth tools, "
            "and gap reports for this project"
        )
        ui.label(
            "Without purge, scoped rows keep their project_id and will "
            "show as orphans in All-Projects views."
        ).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px;"
        )
        with ui.row().classes("gap-2 w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def _confirm():
                state.projects.delete(project_id, cascade=purge_cb.value)
                ui.notify(f"deleted {name}", type="positive")
                dialog.close()
                ui.navigate.to("/projects")

            ui.button("Delete", on_click=_confirm).props(
                "color=negative unelevated"
            )
    dialog.open()


# --- folder picker --------------------------------------------------------


def _open_folder_picker(start: str, on_pick) -> None:
    """Server-side directory browser dialog.

    The workbench runs on the user's machine, so we can list directories
    with plain `Path.iterdir()` instead of relying on a browser file API
    (which can't return a raw server-side path). The user navigates by
    clicking a subfolder (enter it) or a breadcrumb segment (go up),
    then hits "Select this folder" to commit.

    `on_pick(path_str)` is called with the absolute path of whatever
    folder was showing when the user confirmed.
    """
    # Resolve the starting point. If the caller passed a bad path (typo,
    # doesn't exist) we fall back to $HOME so the dialog still opens.
    try:
        current = Path(start).expanduser().resolve()
        if not current.is_dir():
            current = Path.home()
    except Exception:
        current = Path.home()

    state = {"cwd": current}

    with ui.dialog() as dialog, ui.card().style(
        f"width: 640px; max-width: 90vw; "
        f"background: {COLORS['surface']};"
    ):
        ui.label("Select a folder").style(heading_style(size=TEXT_LG))

        # Breadcrumb row: each segment is clickable.
        crumbs = ui.row().classes("w-full items-center gap-1 no-wrap").style(
            f"padding: {SPACE_SM}px; "
            f"background: {COLORS['surface2']}; "
            f"border: 1px solid {COLORS['border']}; "
            "border-radius: 6px; "
            "overflow-x: auto; white-space: nowrap;"
        )

        # The list of folder rows — rebuilt on every navigation.
        listing = ui.column().classes("w-full gap-0").style(
            f"background: {COLORS['surface']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; "
            "max-height: 360px; overflow-y: auto;"
        )

        def navigate(to: Path) -> None:
            state["cwd"] = to
            _render()

        def _render_crumbs() -> None:
            crumbs.clear()
            with crumbs:
                ui.button(
                    "home", on_click=lambda: navigate(Path.home()),
                    icon="home",
                ).props("flat dense").style(
                    f"color: {COLORS['primary']}; font-size: 11px;"
                )
                # Build breadcrumb segments from '/': '/', '/home', '/home/kai', …
                parts = state["cwd"].parts
                accum = Path(parts[0]) if parts else Path("/")
                for i, part in enumerate(parts):
                    if i > 0:
                        accum = accum / part
                    label = "/" if part in ("/", "\\") else part
                    target = accum
                    ui.label("/").style(
                        f"color: {COLORS['text_muted']}; font-size: 12px;"
                    )
                    ui.button(
                        label,
                        on_click=lambda p=target: navigate(p),
                    ).props("flat dense").style(
                        f"color: {COLORS['text']}; "
                        "font-family: monospace; font-size: 12px; "
                        "padding: 2px 6px;"
                    )

        def _render_listing() -> None:
            listing.clear()
            with listing:
                # ".." entry — always useful even at root (where it's a no-op).
                parent = state["cwd"].parent
                if parent != state["cwd"]:
                    _folder_row("..", parent, navigate, is_parent=True)
                try:
                    entries = sorted(
                        (p for p in state["cwd"].iterdir() if p.is_dir()
                         and not p.name.startswith(".")),
                        key=lambda p: p.name.lower(),
                    )
                except PermissionError:
                    ui.label("(permission denied)").style(
                        f"color: {COLORS['error']}; padding: {SPACE_MD}px;"
                    )
                    return
                except OSError as e:
                    ui.label(f"(error: {e})").style(
                        f"color: {COLORS['error']}; padding: {SPACE_MD}px;"
                    )
                    return
                if not entries and parent == state["cwd"]:
                    ui.label("(empty)").style(
                        f"color: {COLORS['text_muted']}; "
                        f"padding: {SPACE_MD}px;"
                    )
                    return
                for sub in entries:
                    _folder_row(sub.name, sub, navigate)

        def _render() -> None:
            _render_crumbs()
            _render_listing()

        _render()

        # Footer: shows the currently-selected absolute path and action buttons.
        with ui.row().classes(
            "w-full items-center no-wrap gap-2"
        ).style(f"margin-top: {SPACE_MD}px;"):
            path_label = ui.label("").style(
                f"color: {COLORS['text_muted']}; "
                "font-family: monospace; font-size: 12px; "
                "flex: 1; overflow: hidden; text-overflow: ellipsis; "
                "white-space: nowrap;"
            )

            def _refresh_footer_label():
                path_label.text = str(state["cwd"])

            # NiceGUI renders the timer in the page; we just poll our
            # mutable state since it's cheap and keeps the footer path in
            # sync as the user navigates.
            ui.timer(0.1, _refresh_footer_label)

            ui.button("Cancel", on_click=dialog.close).props("flat")

            def _confirm():
                on_pick(str(state["cwd"]))
                dialog.close()

            ui.button(
                "Select this folder", on_click=_confirm, icon="check",
            ).props("color=primary unelevated")

    dialog.open()


def _folder_row(label: str, path: Path, navigate, is_parent: bool = False) -> None:
    """One row in the folder picker's listing. Click to descend."""
    with ui.row().classes(
        "w-full items-center no-wrap gap-2 planck-nav-item"
    ).style(
        f"padding: 6px 10px; cursor: pointer; "
        f"border-bottom: 1px solid {COLORS['border']};"
    ).on("click", lambda _=None, p=path: navigate(p)):
        ui.icon(
            "arrow_upward" if is_parent else "folder"
        ).style(
            f"color: "
            f"{COLORS['text_muted'] if is_parent else COLORS['primary']}; "
            "font-size: 16px;"
        )
        ui.label(label).style(
            f"color: {COLORS['text']}; "
            "font-family: monospace; font-size: 13px;"
        )
