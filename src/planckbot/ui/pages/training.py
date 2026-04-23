"""Training page: start training, live loss chart, history."""

import json
from nicegui import ui
from planckbot.ui.theme import COLORS, CARD_STYLE
from planckbot.ui.components.metric_card import metric_card
from planckbot.ui.components.loss_chart import loss_chart, update_loss_chart
from planckbot.ui.components.page_header import page_header
from planckbot.ui.state import get_state
from planckbot.training.trainer import TrainingConfig, get_training_status
from planckbot.experiments.runner import run_experiment


def training_page():
    state = get_state()

    page_header(
        title="Training",
        subtitle="Launch LoRA training runs and watch the loss curve "
                 "live. History of completed runs is listed below.",
    )

    # Current training status
    status = get_training_status()

    if status.is_running:
        ui.label("Training in Progress").style(
            f"color: {COLORS['warning']}; font-size: 18px; font-weight: 600;"
        )
        with ui.row().classes("gap-4"):
            metric_card("Epoch", str(status.current_epoch), icon="repeat", color=COLORS["info"])
            metric_card("Step", str(status.current_step), icon="trending_up", color=COLORS["primary"])
            metric_card("Loss", f"{status.current_loss:.4f}", icon="show_chart", color=COLORS["warning"])
            metric_card("Best Loss", f"{status.best_loss:.4f}", icon="star", color=COLORS["success"])

        # Live loss chart
        if status.experiment_id:
            chart = loss_chart(status.experiment_id)
            ui.timer(
                2.0,
                lambda: update_loss_chart(chart, state.conn, status.experiment_id),
            )

        if status.error:
            ui.label(f"Error: {status.error}").style(f"color: {COLORS['error']};")

    else:
        # Start new training
        ui.label("Start Training Run").style(
            f"color: {COLORS['text']}; font-size: 18px; font-weight: 600;"
        )

        # Select experiment
        experiments = state.experiments.list_all(status="planned")
        if not experiments:
            ui.label(
                "No 'planned' experiments available. Create one from the Experiments page first."
            ).style(f"color: {COLORS['text_muted']}; padding: 20px;")
            return

        exp_options = {e.id: f"{e.name} ({e.experiment_type})" for e in experiments}

        with ui.card().style(CARD_STYLE + " width: 100%;"):
            exp_select = ui.select(
                exp_options, label="Experiment",
                value=experiments[0].id if experiments else None,
            ).style("width: 100%;")

            with ui.row().classes("gap-4 mt-4"):
                model_input = ui.input(
                    "Base Model", value="HuggingFaceTB/SmolLM2-135M-Instruct"
                ).style("flex: 1;")
                device_select = ui.select(
                    ["cpu", "cuda"], value=state.config.device.split()[0],
                    label="Device"
                )

            with ui.row().classes("gap-4"):
                lora_rank = ui.number("LoRA Rank", value=8, min=2, max=64)
                lr = ui.number("Learning Rate", value=1e-4, format="%.1e")
                epochs = ui.number("Epochs", value=3, min=1, max=50)
                batch_size = ui.number("Batch Size", value=4, min=1, max=64)
                max_seq_len = ui.number("Max Seq Length", value=512, min=64, max=2048)

            status_label = ui.label("").style(f"color: {COLORS['text_muted']};")
            chart_container = ui.column().classes("w-full")

            def start_training():
                if not exp_select.value:
                    ui.notify("Select an experiment", type="warning")
                    return

                # Check triples
                exp = state.experiments.get(exp_select.value)
                cfg = exp.config or {}
                tool = cfg.get("tool_name", "")
                triple_count = len(state.triples.get_by_tool(tool)) if tool else state.triples.count_total()

                if triple_count < 2:
                    ui.notify(
                        f"Need at least 2 triples (have {triple_count}). Collect more from Tools page.",
                        type="warning",
                    )
                    return

                config = TrainingConfig(
                    base_model=model_input.value,
                    lora_rank=int(lora_rank.value),
                    learning_rate=float(lr.value),
                    num_epochs=int(epochs.value),
                    batch_size=int(batch_size.value),
                    max_seq_length=int(max_seq_len.value),
                    device=device_select.value,
                )

                status_label.set_text("Starting training...")
                ui.notify("Training started! Loss chart will update every 2s.", type="positive")

                def on_done(ckpt_id):
                    if ckpt_id:
                        status_label.set_text(f"Training complete! Checkpoint: {ckpt_id[:8]}...")
                    else:
                        status_label.set_text("Training failed. Check logs.")

                run_experiment(
                    conn=state.conn,
                    experiment_id=exp_select.value,
                    training_config=config,
                    on_complete=on_done,
                )

                # Show live chart
                with chart_container:
                    chart_container.clear()
                    chart = loss_chart(exp_select.value)
                    ui.timer(
                        2.0,
                        lambda: update_loss_chart(chart, state.conn, exp_select.value),
                    )

            ui.button("Start Training", on_click=start_training, icon="model_training").props(
                "color=primary size=lg"
            )

    # Training history
    ui.separator().style(f"background: {COLORS['border']}; margin: 20px 0;")
    ui.label("Training History").style(
        f"color: {COLORS['text']}; font-size: 18px; font-weight: 600;"
    )

    completed = state.experiments.list_all(status="completed")
    if completed:
        columns = [
            {"name": "name", "label": "Experiment", "field": "name", "align": "left"},
            {"name": "model", "label": "Model", "field": "model", "align": "left"},
            {"name": "tool", "label": "Tool", "field": "tool", "align": "left"},
            {"name": "savings", "label": "Token Savings", "field": "savings", "align": "left"},
            {"name": "date", "label": "Completed", "field": "date", "align": "left"},
        ]
        rows = []
        for e in completed:
            cfg = e.config or {}
            met = e.metrics or {}
            rows.append({
                "name": e.name,
                "model": cfg.get("model_name", "—").split("/")[-1],
                "tool": cfg.get("tool_name", "—"),
                "savings": f"{met.get('token_savings_pct', '—')}%",
                "date": e.completed_at[:10] if e.completed_at else "—",
            })
        ui.table(columns=columns, rows=rows, row_key="name").style(
            f"background-color: {COLORS['surface']}; color: {COLORS['text']}; width: 100%;"
        )
    else:
        ui.label("No completed training runs yet.").style(f"color: {COLORS['text_muted']};")
