"""Tools page: tool cards, triple browser, manual collection form."""

import json
from nicegui import ui
from planckbot.ui.theme import COLORS, CARD_STYLE
from planckbot.ui.components.triple_viewer import triple_viewer
from planckbot.ui.state import get_state
from planckbot.experiments.metrics import count_tokens_approx


def _tool_card(tool_def, triple_count: int):
    """Card displaying a registered tool."""
    with ui.card().style(
        f"background-color: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        "border-radius: 12px; padding: 16px; min-width: 280px;"
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon("build").style(f"color: {COLORS['primary']};")
            ui.label(tool_def.name).style(
                f"color: {COLORS['text']}; font-weight: 700; font-size: 16px;"
            )
            ui.label(tool_def.category).style(
                f"color: {COLORS['text_muted']}; font-size: 11px; "
                f"background: {COLORS['surface2']}; padding: 1px 8px; border-radius: 4px;"
            )
        ui.label(tool_def.description).style(
            f"color: {COLORS['text_muted']}; font-size: 13px; margin: 4px 0;"
        )
        with ui.row().classes("items-center gap-4"):
            ui.label(f"{triple_count} triples").style(
                f"color: {COLORS['info']}; font-size: 12px;"
            )
            if tool_def.input_schema:
                params = ", ".join(tool_def.input_schema.keys())
                ui.label(f"Params: {params}").style(
                    f"color: {COLORS['text_muted']}; font-size: 11px;"
                )


def _collection_form():
    """Manual triple collection: select tool, input params, run, save."""
    state = get_state()

    ui.label("Collect Triple").style(
        f"color: {COLORS['text']}; font-size: 18px; font-weight: 600; margin-top: 20px;"
    )

    with ui.card().style(CARD_STYLE + " width: 100%;"):
        tool_select = ui.select(
            state.registry.names(),
            value=state.registry.names()[0] if state.registry.names() else "",
            label="Tool",
        ).style("width: 300px;")

        input_area = ui.textarea(
            "Input JSON",
            placeholder='{"query": "test", "path": "."}',
        ).style("width: 100%; font-family: monospace;")

        output_container = ui.column().classes("w-full")
        current_output = {"value": "", "triple_id": None}

        def run_tool():
            output_container.clear()
            try:
                params = json.loads(input_area.value) if input_area.value.strip() else {}
            except json.JSONDecodeError:
                ui.notify("Invalid JSON input", type="negative")
                return

            tool_name = tool_select.value
            try:
                result = state.registry.execute(tool_name, **params)
                current_output["value"] = result

                # Auto-save triple
                triple = state.triples.add(
                    tool_name=tool_name,
                    input_data=params,
                    output_data=result,
                    source="manual",
                )
                current_output["triple_id"] = triple.id

                with output_container:
                    ui.label("Output").style(
                        f"color: {COLORS['success']}; font-weight: 600; margin-top: 8px;"
                    )
                    tokens = count_tokens_approx(result)
                    ui.label(f"{tokens} tokens").style(
                        f"color: {COLORS['text_muted']}; font-size: 12px;"
                    )
                    ui.code(result[:5000], language="json").style(
                        "max-height: 300px; overflow: auto; width: 100%;"
                    )

                    # Filtered output area
                    ui.label("Filtered Output (edit to mark what should be kept)").style(
                        f"color: {COLORS['warning']}; font-weight: 600; margin-top: 12px;"
                    )
                    filtered_area = ui.textarea(value="").style(
                        "width: 100%; min-height: 100px; font-family: monospace;"
                    )

                    def save_filtered():
                        if current_output["triple_id"] and filtered_area.value.strip():
                            state.triples.update_filtered(
                                current_output["triple_id"], filtered_area.value
                            )
                            ui.notify("Filtered output saved!", type="positive")

                    ui.button(
                        "Save & Mark Filtered", on_click=save_filtered, icon="filter_alt"
                    ).props("color=warning")

                ui.notify(f"Triple saved (ID: {triple.id[:8]}...)", type="positive")

            except Exception as e:
                with output_container:
                    ui.label(f"Error: {e}").style(f"color: {COLORS['error']};")

        ui.button("Run Tool", on_click=run_tool, icon="play_arrow").props("color=primary")


def tools_page():
    state = get_state()

    ui.label("Tools & Triple Collection").style(
        f"color: {COLORS['text']}; font-size: 24px; font-weight: 700;"
    )

    ui.separator().style(f"background: {COLORS['border']}; margin: 12px 0;")

    # Tool cards
    counts = state.triples.count_by_tool()
    with ui.row().classes("gap-4 flex-wrap"):
        for tool in state.registry.list_tools():
            _tool_card(tool, counts.get(tool.name, 0))

    # Collection form
    _collection_form()

    ui.separator().style(f"background: {COLORS['border']}; margin: 20px 0;")

    # Triple browser
    ui.label("Triple Browser").style(
        f"color: {COLORS['text']}; font-size: 18px; font-weight: 600;"
    )

    # Filter by tool
    tool_names = ["All"] + state.registry.names()
    tool_filter = ui.select(tool_names, value="All", label="Filter by Tool").style("width: 200px;")

    triples_container = ui.column().classes("w-full gap-2")

    def refresh_triples():
        triples_container.clear()
        with triples_container:
            if tool_filter.value == "All":
                triples = state.triples.list_all(limit=20)
            else:
                triples = state.triples.get_by_tool(tool_filter.value, limit=20)

            if not triples:
                ui.label("No triples collected yet.").style(f"color: {COLORS['text_muted']};")
            else:
                for t in triples:
                    triple_viewer(t)

    tool_filter.on_value_change(lambda _: refresh_triples())
    refresh_triples()
