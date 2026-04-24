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
        # Header row
        with ui.row().classes("w-full items-center no-wrap").style(
            f"padding: {SPACE_MD}px {SPACE_LG}px; "
            f"background: {COLORS['surface2']}; "
            f"border-bottom: 1px solid {COLORS['border']};"
        ):
            for label, flex in [
                ("", "flex: 0 0 20px"),
                ("NAME", "flex: 2; min-width: 140px"),
                ("PATH", "flex: 3; min-width: 240px"),
                ("TRIPLES", "flex: 0 0 90px"),
                ("CKPTS", "flex: 0 0 70px"),
                ("ROUTING", "flex: 0 0 140px"),
                ("LAST USED", "flex: 0 0 120px"),
                ("ACTIONS", "flex: 0 0 240px"),
            ]:
                ui.label(label).style(
                    label_style() + f" {flex};"
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

        # Stats
        ui.label(f"{triples}").style(
            f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
            "font-variant-numeric: tabular-nums; flex: 0 0 90px;"
        )
        ui.label(f"{ckpts}").style(
            f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
            "font-variant-numeric: tabular-nums; flex: 0 0 70px;"
        )
        _routing_control(project)
        ui.label(last_used).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "flex: 0 0 120px;"
        )

        # Actions
        with ui.row().classes("items-center gap-1 no-wrap").style(
            "flex: 0 0 240px;"
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
            "flex: 0 0 140px;"
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
        "flex: 0 0 140px; font-size: 13px;"
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
