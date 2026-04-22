"""Triple viewer component: side-by-side input/output with token counts."""

import json
from nicegui import ui
from planckbot.db.models import Triple
from planckbot.ui.theme import COLORS


def _format_json(data: str) -> str:
    """Try to pretty-print JSON, fallback to raw string."""
    try:
        return json.dumps(json.loads(data), indent=2)
    except (json.JSONDecodeError, TypeError):
        return data


def triple_viewer(triple: Triple):
    """Display a single triple with input/output side by side."""
    with ui.card().style(
        f"background-color: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        "border-radius: 12px; padding: 16px; width: 100%;"
    ):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label(f"Tool: {triple.tool_name}").style(
                f"color: {COLORS['primary']}; font-weight: 600;"
            )
            ui.label(f"Source: {triple.source}").style(
                f"color: {COLORS['text_muted']}; font-size: 12px;"
            )
            ui.label(triple.created_at[:16] if triple.created_at else "").style(
                f"color: {COLORS['text_muted']}; font-size: 12px;"
            )

        with ui.row().classes("w-full gap-4"):
            # Input panel
            with ui.column().classes("flex-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Input").style(
                        f"color: {COLORS['info']}; font-weight: 600; font-size: 13px;"
                    )
                    if triple.input_tokens:
                        ui.label(f"{triple.input_tokens} tokens").style(
                            f"color: {COLORS['text_muted']}; font-size: 11px; "
                            f"background: {COLORS['surface2']}; padding: 1px 6px; border-radius: 4px;"
                        )
                ui.code(_format_json(triple.input_data), language="json").style(
                    "max-height: 200px; overflow: auto; width: 100%;"
                )

            # Output panel
            with ui.column().classes("flex-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Output").style(
                        f"color: {COLORS['warning']}; font-weight: 600; font-size: 13px;"
                    )
                    if triple.output_tokens:
                        ui.label(f"{triple.output_tokens} tokens").style(
                            f"color: {COLORS['text_muted']}; font-size: 11px; "
                            f"background: {COLORS['surface2']}; padding: 1px 6px; border-radius: 4px;"
                        )
                ui.code(_format_json(triple.output_data), language="json").style(
                    "max-height: 200px; overflow: auto; width: 100%;"
                )

            # Filtered panel (if exists)
            if triple.filtered_output:
                with ui.column().classes("flex-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label("Filtered").style(
                            f"color: {COLORS['success']}; font-weight: 600; font-size: 13px;"
                        )
                        if triple.filtered_tokens:
                            savings = ""
                            if triple.output_tokens and triple.output_tokens > 0:
                                pct = round(
                                    (1 - triple.filtered_tokens / triple.output_tokens) * 100
                                )
                                savings = f" ({pct}% saved)"
                            ui.label(f"{triple.filtered_tokens} tokens{savings}").style(
                                f"color: {COLORS['success']}; font-size: 11px; "
                                f"background: {COLORS['surface2']}; padding: 1px 6px; border-radius: 4px;"
                            )
                    ui.code(_format_json(triple.filtered_output), language="json").style(
                        "max-height: 200px; overflow: auto; width: 100%;"
                    )
