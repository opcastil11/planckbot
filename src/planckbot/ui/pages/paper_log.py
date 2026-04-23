"""Paper Log page: chronological feed, filters, export."""

import json
from pathlib import Path
from nicegui import ui
from planckbot.ui.theme import COLORS, CARD_STYLE
from planckbot.ui.components.page_header import page_header
from planckbot.ui.components.empty_state import empty_state
from planckbot.ui.state import get_state
from planckbot.paper.export import export_full_report


ENTRY_ICONS = {
    "observation": "visibility",
    "result": "check_circle",
    "decision": "gavel",
    "question": "help",
}

ENTRY_COLORS = {
    "observation": COLORS["info"],
    "result": COLORS["success"],
    "decision": COLORS["warning"],
    "question": COLORS["secondary"],
}


def paper_log_page():
    state = get_state()

    # We need the buttons to attach to a row the page_header renders, so
    # define them first and pass as actions.
    def _actions():
        # Add entry + export buttons (dialog/export defined below in closure)
        pass

    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-1"):
            ui.label("Paper log").style(
                f"color: {COLORS['text']}; font-size: 24px; font-weight: 700; "
                "letter-spacing: -0.4px;"
            )
            ui.label(
                "Chronological journal of observations, results, decisions, "
                "and open questions across all experiments."
            ).style(
                f"color: {COLORS['text_muted']}; font-size: 14px; line-height: 1.5;"
            )
        with ui.row().classes("gap-2"):
            # Add entry button
            def add_entry_dialog():
                with ui.dialog() as dialog, ui.card().style(
                    f"background-color: {COLORS['surface']}; min-width: 500px; "
                    f"border: 1px solid {COLORS['border']};"
                ):
                    ui.label("New Log Entry").style(
                        f"color: {COLORS['text']}; font-size: 20px; font-weight: 700;"
                    )
                    entry_type = ui.select(
                        ["observation", "result", "decision", "question"],
                        value="observation", label="Type",
                    ).style("width: 100%;")
                    title = ui.input("Title").style("width: 100%;")
                    content = ui.textarea(
                        "Content (Markdown)", placeholder="Write your observation..."
                    ).style("width: 100%; min-height: 150px;")
                    tags = ui.input("Tags (comma-separated)").style("width: 100%;")

                    # Link to experiment
                    exps = state.experiments.list_all()
                    exp_options = {"": "None"} | {e.id: e.name for e in exps}
                    exp_link = ui.select(exp_options, value="", label="Link to Experiment").style("width: 100%;")

                    with ui.row().classes("justify-end gap-2 mt-4"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        def save():
                            tag_list = [t.strip() for t in tags.value.split(",") if t.strip()] if tags.value else []
                            state.paper_log.add_entry(
                                title=title.value,
                                content=content.value,
                                entry_type=entry_type.value,
                                experiment_id=exp_link.value or None,
                                tags=tag_list,
                            )
                            dialog.close()
                            ui.navigate.to("/paper-log")
                        ui.button("Save", on_click=save).props("color=primary")
                dialog.open()

            ui.button("New Entry", on_click=add_entry_dialog, icon="add").props("color=primary")

            def export_report():
                experiments = state.experiments.list_all()
                entries = state.paper_log.list_entries()
                output_path = state.config.data_dir / "exports" / "report.md"
                report = export_full_report(experiments, entries, output_path)
                ui.notify(f"Report exported to {output_path}", type="positive")

            ui.button("Export Report", on_click=export_report, icon="download").props("flat")

    ui.separator().style(f"background: {COLORS['border']}; margin: 12px 0;")

    # Filters
    with ui.row().classes("gap-4"):
        type_filter = ui.select(
            ["All", "observation", "result", "decision", "question"],
            value="All", label="Type",
        ).style("width: 160px;")

    entries_container = ui.column().classes("w-full gap-3")

    def refresh_entries():
        entries_container.clear()
        with entries_container:
            entry_type = type_filter.value if type_filter.value != "All" else None
            entries = state.paper_log.list_entries(entry_type=entry_type)

            if not entries:
                empty_state(
                    title="Nothing logged yet",
                    hint="Capture observations, results, decisions, and "
                         "open questions here as you experiment. Use it "
                         "the way you'd use a real research paper log.",
                    icon="menu_book",
                )
                return

            for entry in entries:
                icon = ENTRY_ICONS.get(entry.entry_type, "note")
                color = ENTRY_COLORS.get(entry.entry_type, COLORS["text_muted"])

                with ui.card().style(
                    f"background-color: {COLORS['surface']}; "
                    f"border: 1px solid {COLORS['border']}; "
                    f"border-left: 4px solid {color}; "
                    "border-radius: 8px; padding: 16px; width: 100%;"
                ):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon(icon).style(f"color: {color};")
                        ui.label(entry.title).style(
                            f"color: {COLORS['text']}; font-weight: 600; font-size: 15px;"
                        )
                        ui.label(entry.entry_type).style(
                            f"color: {color}; font-size: 11px; "
                            f"background: {color}22; padding: 1px 8px; border-radius: 4px;"
                        )
                        ui.label(entry.created_at[:16] if entry.created_at else "").style(
                            f"color: {COLORS['text_muted']}; font-size: 12px; margin-left: auto;"
                        )

                    ui.markdown(entry.content).style(
                        f"color: {COLORS['text_muted']}; font-size: 13px; margin-top: 8px;"
                    )

                    if entry.tags:
                        with ui.row().classes("gap-1 mt-2"):
                            for tag in entry.tags:
                                ui.label(tag).style(
                                    f"color: {COLORS['text_muted']}; font-size: 10px; "
                                    f"background: {COLORS['surface2']}; "
                                    "padding: 1px 6px; border-radius: 4px;"
                                )

                    if entry.experiment_id:
                        exp = state.experiments.get(entry.experiment_id)
                        if exp:
                            ui.label(f"Linked: {exp.name}").style(
                                f"color: {COLORS['primary']}; font-size: 11px; margin-top: 4px; cursor: pointer;"
                            ).on("click", lambda _, eid=exp.id: ui.navigate.to(f"/experiments/{eid}"))

    type_filter.on_value_change(lambda _: refresh_entries())
    refresh_entries()
