"""Models page: checkpoint table, comparison, activate/delete."""

import json
from nicegui import ui
from planckbot.ui.theme import COLORS, CARD_STYLE
from planckbot.ui.components.status_badge import status_badge
from planckbot.ui.state import get_state


def models_page():
    state = get_state()

    ui.label("Model Checkpoints").style(
        f"color: {COLORS['text']}; font-size: 24px; font-weight: 700;"
    )

    ui.separator().style(f"background: {COLORS['border']}; margin: 12px 0;")

    checkpoints = state.checkpoints.list_all()
    if not checkpoints:
        ui.label("No checkpoints yet. Train a model from the Training page.").style(
            f"color: {COLORS['text_muted']}; padding: 40px; text-align: center;"
        )
        return

    # Compare selection
    selected_ids: list[str] = []

    def toggle_select(ckpt_id: str, checked: bool):
        if checked and ckpt_id not in selected_ids:
            selected_ids.append(ckpt_id)
        elif not checked and ckpt_id in selected_ids:
            selected_ids.remove(ckpt_id)

    compare_container = ui.column().classes("w-full")

    def show_compare():
        compare_container.clear()
        if len(selected_ids) >= 2:
            ckpts = state.checkpoints.compare(selected_ids)
            with compare_container:
                ui.label("Comparison").style(
                    f"color: {COLORS['text']}; font-size: 18px; font-weight: 600;"
                )
                # Build comparison table
                columns = [
                    {"name": "field", "label": "Field", "field": "field", "align": "left"},
                ]
                for i, c in enumerate(ckpts):
                    columns.append({"name": f"c{i}", "label": c.name, "field": f"c{i}", "align": "left"})

                fields = [
                    ("Base Model", lambda c: c.base_model.split("/")[-1]),
                    ("Tool", lambda c: c.tool_name or "—"),
                    ("Strategy", lambda c: c.strategy or "—"),
                    ("LoRA Rank", lambda c: str((c.lora_config or {}).get("r", "—"))),
                    ("Adapter Size", lambda c: f"{c.adapter_size_mb or 0:.1f} MB"),
                    ("Triples", lambda c: str(c.num_triples or "—")),
                    ("Active", lambda c: "Yes" if c.is_active else "No"),
                ]
                if any(c.eval_metrics for c in ckpts):
                    fields.append(("Accuracy", lambda c: str((c.eval_metrics or {}).get("accuracy", "—"))))
                    fields.append(("Token Savings", lambda c: f"{(c.eval_metrics or {}).get('token_savings_pct', '—')}%"))

                rows = []
                for label, fn in fields:
                    row = {"field": label}
                    for i, c in enumerate(ckpts):
                        row[f"c{i}"] = fn(c)
                    rows.append(row)

                ui.table(columns=columns, rows=rows, row_key="field").style(
                    f"background-color: {COLORS['surface']}; color: {COLORS['text']}; width: 100%;"
                )

    # Checkpoint cards
    for ckpt in checkpoints:
        with ui.card().style(
            f"background-color: {COLORS['surface']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-left: 4px solid {COLORS['success'] if ckpt.is_active else COLORS['border']}; "
            "border-radius: 8px; padding: 16px; width: 100%;"
        ):
            with ui.row().classes("w-full items-center gap-4"):
                ui.checkbox(on_change=lambda e, cid=ckpt.id: toggle_select(cid, e.value))
                with ui.column().classes("flex-1 gap-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(ckpt.name).style(
                            f"color: {COLORS['text']}; font-weight: 600; font-size: 15px;"
                        )
                        if ckpt.is_active:
                            ui.label("ACTIVE").style(
                                f"color: {COLORS['success']}; font-size: 11px; font-weight: 700; "
                                f"background: {COLORS['success']}22; padding: 1px 8px; border-radius: 4px;"
                            )
                    ui.label(
                        f"{ckpt.base_model.split('/')[-1]} | "
                        f"Tool: {ckpt.tool_name or '—'} | "
                        f"LoRA r={(ckpt.lora_config or {}).get('r', '—')} | "
                        f"Size: {ckpt.adapter_size_mb or 0:.1f}MB | "
                        f"{ckpt.num_triples or 0} triples"
                    ).style(f"color: {COLORS['text_muted']}; font-size: 12px;")

                    if ckpt.eval_metrics:
                        met = ckpt.eval_metrics
                        ui.label(
                            f"Accuracy: {met.get('accuracy', '—')} | "
                            f"Savings: {met.get('token_savings_pct', '—')}% | "
                            f"Latency: {met.get('latency_ms', '—')}ms"
                        ).style(f"color: {COLORS['info']}; font-size: 12px;")

                with ui.row().classes("gap-2"):
                    if not ckpt.is_active:
                        def activate(cid=ckpt.id):
                            state.checkpoints.activate(cid)
                            ui.navigate.to("/models")
                        ui.button("Activate", on_click=activate, icon="check").props("flat dense")
                    else:
                        def deactivate(cid=ckpt.id):
                            state.checkpoints.deactivate(cid)
                            ui.navigate.to("/models")
                        ui.button("Deactivate", on_click=deactivate, icon="close").props("flat dense")

                    def delete_ckpt(cid=ckpt.id):
                        state.checkpoints.delete(cid, delete_files=True)
                        ui.navigate.to("/models")
                    ui.button(icon="delete", on_click=delete_ckpt).props("flat dense color=negative")

    ui.button("Compare Selected", on_click=show_compare, icon="compare").props("flat").style(
        "margin-top: 12px;"
    )

    compare_container
