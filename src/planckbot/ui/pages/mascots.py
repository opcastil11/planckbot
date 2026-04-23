"""Mascot gallery page — one PlanckBot per registered tool."""

from __future__ import annotations

from nicegui import ui

from planckbot.ui.mascots import mascot_svg, style_for
from planckbot.ui.state import get_state
from planckbot.ui.theme import COLORS


def mascots_page():
    state = get_state()

    # Collect unique tool names we know about: builtin registry + tools that
    # have triples in the DB + tools with checkpoints.
    tools = set(state.registry.names())
    for row in state.conn.execute("SELECT DISTINCT tool_name FROM triples"):
        tools.add(row["tool_name"])
    for row in state.conn.execute(
        "SELECT DISTINCT tool_name FROM model_checkpoints WHERE tool_name IS NOT NULL"
    ):
        tools.add(row["tool_name"])
    tools = sorted(t for t in tools if t)

    # Header
    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-1"):
            ui.label("PlanckBots Mascots").style(
                f"color: {COLORS['text']}; font-size: 24px; font-weight: 700; "
                "letter-spacing: -0.4px;"
            )
            ui.label(
                f"{len(tools)} tool{'s' if len(tools) != 1 else ''} — each one gets "
                "its own little bot. Same tool name always produces the same mascot."
            ).style(f"color: {COLORS['text_muted']}; font-size: 13px;")

    ui.separator().style(f"background: {COLORS['border']}; margin: 12px 0 20px 0;")

    if not tools:
        ui.label(
            "No tools registered yet. Call a tool through the proxy, add a "
            "builtin, or run a training job and the mascots will appear here."
        ).style(
            f"color: {COLORS['text_muted']}; padding: 40px; text-align: center;"
        )
        return

    # Stats counts per tool, so we can show activity under each mascot.
    triples_by_tool: dict[str, int] = {
        r["tool_name"]: r["n"]
        for r in state.conn.execute(
            "SELECT tool_name, COUNT(*) AS n FROM triples GROUP BY tool_name"
        )
    }
    ckpts_by_tool: dict[str, int] = {
        r["tool_name"]: r["n"]
        for r in state.conn.execute(
            "SELECT tool_name, COUNT(*) AS n FROM model_checkpoints "
            "WHERE tool_name IS NOT NULL GROUP BY tool_name"
        )
    }

    # Grid of mascot cards
    with ui.row().classes("w-full gap-4 flex-wrap"):
        for tool_name in tools:
            s = style_for(tool_name)
            triples = triples_by_tool.get(tool_name, 0)
            ckpts = ckpts_by_tool.get(tool_name, 0)

            with ui.card().style(
                f"width: 220px; background: linear-gradient(180deg, "
                f"{COLORS['surface']} 0%, {COLORS['bg']} 140%); "
                f"border: 1px solid {COLORS['border']}; "
                "border-radius: 16px; padding: 16px; "
                "display: flex; flex-direction: column; align-items: center; "
                "transition: transform 150ms ease, border-color 150ms ease; "
                "cursor: default;"
            ).classes("planck-mascot-card"):
                ui.html(mascot_svg(tool_name, size=120, title=True))
                ui.label(tool_name).style(
                    f"color: {COLORS['text']}; font-size: 15px; "
                    "font-weight: 600; margin-top: 8px; text-align: center; "
                    "word-break: break-word;"
                )
                ui.label(f"specialty: {s.accessory}").style(
                    f"color: {COLORS['text_muted']}; font-size: 11px; "
                    "letter-spacing: 0.4px; text-transform: uppercase; margin-top: 2px;"
                )
                with ui.row().classes("gap-3 justify-center").style("margin-top: 8px;"):
                    ui.html(
                        f'<span style="color: {COLORS["primary"]}; font-weight: 600;">'
                        f'{triples}</span>'
                        f'<span style="color: {COLORS["text_muted"]}; font-size: 11px; '
                        f'margin-left: 4px;">triples</span>'
                    )
                    ui.html(
                        f'<span style="color: {COLORS["brass"]}; font-weight: 600;">'
                        f'{ckpts}</span>'
                        f'<span style="color: {COLORS["text_muted"]}; font-size: 11px; '
                        f'margin-left: 4px;">ckpts</span>'
                    )

    # Hover effect
    ui.add_head_html("""
    <style>
      .planck-mascot-card:hover {
        transform: translateY(-3px);
        border-color: #2F6B8E !important;
        box-shadow: 0 6px 24px rgba(95, 212, 163, 0.12);
      }
    </style>
    """)
