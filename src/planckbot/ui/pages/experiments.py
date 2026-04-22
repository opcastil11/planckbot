"""Experiments page: list, create, detail, compare."""

import json
from nicegui import ui
from planckbot.ui.theme import COLORS, CARD_STYLE
from planckbot.ui.components.metric_card import metric_card
from planckbot.ui.components.status_badge import status_badge
from planckbot.ui.components.comparison_table import comparison_table
from planckbot.ui.state import get_state


def _create_dialog():
    """Dialog for creating a new experiment."""
    state = get_state()

    with ui.dialog() as dialog, ui.card().style(
        f"background-color: {COLORS['surface']}; min-width: 500px; "
        f"border: 1px solid {COLORS['border']};"
    ):
        ui.label("New Experiment").style(
            f"color: {COLORS['text']}; font-size: 20px; font-weight: 700;"
        )

        name = ui.input("Name", placeholder="e.g., filter_file_search_v1").style("width: 100%;")
        hypothesis = ui.textarea("Hypothesis", placeholder="e.g., LoRA-8 on SmolLM2-135M can filter file_search output by 60%").style("width: 100%;")
        exp_type = ui.select(
            ["filter_output", "compress_input", "short_circuit"],
            value="filter_output", label="Experiment Type",
        ).style("width: 100%;")
        tool_name = ui.select(
            state.registry.names(), label="Tool",
            value=state.registry.names()[0] if state.registry.names() else "",
        ).style("width: 100%;")
        model_name = ui.input(
            "Base Model", value="HuggingFaceTB/SmolLM2-135M-Instruct"
        ).style("width: 100%;")

        with ui.row().classes("gap-4"):
            lora_rank = ui.number("LoRA Rank", value=8, min=2, max=64)
            lr = ui.number("Learning Rate", value=1e-4, format="%.1e")
            epochs = ui.number("Epochs", value=3, min=1, max=50)
            batch_size = ui.number("Batch Size", value=4, min=1, max=64)

        with ui.row().classes("justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            def create():
                config = {
                    "model_name": model_name.value,
                    "tool_name": tool_name.value,
                    "strategy": exp_type.value,
                    "lora_rank": int(lora_rank.value),
                    "learning_rate": float(lr.value),
                    "epochs": int(epochs.value),
                    "batch_size": int(batch_size.value),
                }
                state.experiments.create(
                    name=name.value,
                    hypothesis=hypothesis.value,
                    experiment_type=exp_type.value,
                    config=config,
                )
                dialog.close()
                ui.navigate.to("/experiments")

            ui.button("Create", on_click=create).props("color=primary")

    return dialog


def _detail_view(exp_id: str):
    """Detail view for a single experiment."""
    state = get_state()
    exp = state.experiments.get(exp_id)
    if not exp:
        ui.label("Experiment not found.").style(f"color: {COLORS['error']};")
        return

    with ui.row().classes("items-center gap-4"):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/experiments")).props("flat")
        ui.label(exp.name).style(
            f"color: {COLORS['text']}; font-size: 24px; font-weight: 700;"
        )
        status_badge(exp.status)

    if exp.hypothesis:
        ui.label(f"Hypothesis: {exp.hypothesis}").style(
            f"color: {COLORS['text_muted']}; font-style: italic; margin: 8px 0;"
        )

    # Metrics cards
    met = exp.metrics or {}
    with ui.row().classes("gap-4 flex-wrap"):
        metric_card("Token Savings", f"{met.get('token_savings_pct', '—')}%",
                     icon="savings", color=COLORS["success"])
        metric_card("Accuracy", str(met.get("accuracy", "—")),
                     icon="check_circle", color=COLORS["info"])
        metric_card("Latency", f"{met.get('latency_ms', '—')}ms",
                     icon="speed", color=COLORS["warning"])
        metric_card("Triples", str(met.get("num_triples", "—")),
                     icon="data_object", color=COLORS["primary"])

    # Config
    ui.label("Configuration").style(
        f"color: {COLORS['text']}; font-size: 16px; font-weight: 600; margin-top: 16px;"
    )
    ui.code(json.dumps(exp.config or {}, indent=2), language="json").style("width: 100%;")

    # Observations
    ui.label("Observations").style(
        f"color: {COLORS['text']}; font-size: 16px; font-weight: 600; margin-top: 16px;"
    )
    obs_area = ui.textarea(value=exp.observations or "").style(
        "width: 100%; min-height: 100px;"
    )
    def save_obs():
        state.experiments.update_observations(exp.id, obs_area.value)
        ui.notify("Observations saved", type="positive")
    ui.button("Save Observations", on_click=save_obs, icon="save").props("flat")

    # Linked triples
    triples = state.triples.get_by_experiment(exp.id)
    if triples:
        ui.label(f"Linked Triples ({len(triples)})").style(
            f"color: {COLORS['text']}; font-size: 16px; font-weight: 600; margin-top: 16px;"
        )


def experiments_page():
    state = get_state()

    # Header
    with ui.row().classes("w-full items-center justify-between"):
        ui.label("Experiments").style(
            f"color: {COLORS['text']}; font-size: 24px; font-weight: 700;"
        )
        with ui.row().classes("gap-2"):
            dialog = _create_dialog()
            ui.button("New Experiment", on_click=dialog.open, icon="add").props("color=primary")

    ui.separator().style(f"background: {COLORS['border']}; margin: 12px 0;")

    # Experiments list
    experiments = state.experiments.list_all()
    if not experiments:
        ui.label("No experiments yet. Click 'New Experiment' to get started.").style(
            f"color: {COLORS['text_muted']}; padding: 40px; text-align: center;"
        )
        return

    # Compare selection
    selected_ids: list[str] = []

    def toggle_compare(exp_id: str, checked: bool):
        if checked and exp_id not in selected_ids:
            selected_ids.append(exp_id)
        elif not checked and exp_id in selected_ids:
            selected_ids.remove(exp_id)

    compare_container = ui.column().classes("w-full")

    def show_compare():
        compare_container.clear()
        if len(selected_ids) >= 2:
            exps = state.experiments.compare(selected_ids)
            with compare_container:
                ui.label("Comparison").style(
                    f"color: {COLORS['text']}; font-size: 18px; font-weight: 600;"
                )
                comparison_table(exps)

    # Table
    for exp in experiments:
        with ui.card().style(
            f"background-color: {COLORS['surface']}; "
            f"border: 1px solid {COLORS['border']}; "
            "border-radius: 8px; padding: 12px; width: 100%; cursor: pointer;"
        ):
            with ui.row().classes("w-full items-center gap-4"):
                ui.checkbox(on_change=lambda e, eid=exp.id: toggle_compare(eid, e.value))
                with ui.column().classes("flex-1 gap-0").on(
                    "click", lambda _, eid=exp.id: ui.navigate.to(f"/experiments/{eid}")
                ):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(exp.name).style(
                            f"color: {COLORS['text']}; font-weight: 600; font-size: 15px;"
                        )
                        status_badge(exp.status)
                    ui.label(
                        f"{exp.experiment_type} | {(exp.config or {}).get('tool_name', '—')} | "
                        f"Created {exp.created_at[:10] if exp.created_at else '—'}"
                    ).style(f"color: {COLORS['text_muted']}; font-size: 12px;")
                    if exp.hypothesis:
                        ui.label(exp.hypothesis[:120]).style(
                            f"color: {COLORS['text_muted']}; font-size: 12px; font-style: italic;"
                        )

    ui.button("Compare Selected", on_click=show_compare, icon="compare").props("flat").style(
        "margin-top: 12px;"
    )

    compare_container
