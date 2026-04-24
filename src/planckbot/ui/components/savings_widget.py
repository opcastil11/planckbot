"""Savings widget: tokens + estimated USD at a user-selected model rate.

Designed to be the first thing a user sees after the hero. Headline
number is the cumulative savings across all `proxy:intervene` triples;
the model dropdown re-renders the dollar figure. Per-tool breakdown
shows where the savings are (or aren't) coming from.
"""

from __future__ import annotations

from nicegui import ui

from planckbot.pricing import DEFAULT_MODEL, estimate_cost, get_model, list_models
from planckbot.ui.theme import (
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
    number_fmt,
)


def _savings_by_tool(
    conn, project_id: str | None = None,
) -> list[tuple[str, int, int, int]]:
    """Aggregate per-tool savings for intervene triples with filtered_output.

    Returns list of (tool_name, n_interventions, raw_tokens, filtered_tokens)
    sorted by savings (raw-filtered) descending. When `project_id` is set,
    restricts to that project's triples only.
    """
    q = (
        "SELECT tool_name, "
        "       COUNT(*) AS n, "
        "       COALESCE(SUM(output_tokens), 0) AS raw, "
        "       COALESCE(SUM(filtered_tokens), 0) AS filt "
        "FROM triples "
        "WHERE source = 'proxy:intervene' "
        "  AND filtered_output IS NOT NULL "
        "  AND output_tokens IS NOT NULL "
        "  AND filtered_tokens IS NOT NULL"
    )
    args: list = []
    if project_id is not None:
        q += " AND project_id = ?"
        args.append(project_id)
    q += " GROUP BY tool_name ORDER BY (raw - filt) DESC"
    rows = conn.execute(q, args).fetchall()
    return [(r[0], int(r[1]), int(r[2]), int(r[3])) for r in rows]


def savings_widget(state) -> None:
    """Render the savings widget with model selector and per-tool breakdown."""
    pid = state.active_project_id()
    savings = state.triples.token_savings(
        source="proxy:intervene", project_id=pid,
    )
    per_tool = _savings_by_tool(state.conn, project_id=pid)

    saved = savings["saved"]
    n_intervene = savings["intervene_count"]
    raw = savings["raw_tokens"]
    filtered = savings["filtered_tokens"]
    pct = (saved / raw * 100) if raw else 0
    sign = "+" if saved >= 0 else ""

    # Outer card
    with ui.column().classes("w-full").style(
        f"background: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px; "
        f"padding: {SPACE_LG}px; "
        f"margin-bottom: {SPACE_LG}px;"
    ):
        # Header row with title + model selector
        with ui.row().classes("w-full items-center justify-between no-wrap flex-wrap gap-3").style(
            f"margin-bottom: {SPACE_MD}px;"
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("savings").style(
                    f"color: {COLORS['primary']}; font-size: 20px;"
                )
                ui.label("Savings").style(heading_style(size=TEXT_LG))
            # Model selector for cost estimation
            model_opts = {m["id"]: m["label"] for m in list_models()}
            state_key = "_pricing_model_id"
            if not hasattr(state, state_key):
                setattr(state, state_key, DEFAULT_MODEL)
            current = getattr(state, state_key)

            with ui.row().classes("items-center gap-2"):
                ui.label("Priced at").style(
                    f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px;"
                )
                sel = ui.select(
                    model_opts,
                    value=current,
                    on_change=lambda e, s=state:
                        setattr(s, state_key, e.value) or ui.navigate.reload(),
                ).style("min-width: 180px;")
                sel.tooltip(
                    "Estimated USD cost uses this model's per-million "
                    "input-token rate. Change to see what your savings "
                    "would mean at a different price point."
                )

        if n_intervene == 0:
            _empty_savings(state)
            return

        # Big headline number
        quote = estimate_cost(saved, model_id=current)
        with ui.row().classes("w-full items-baseline gap-6 flex-wrap").style(
            f"padding: {SPACE_SM}px 0 {SPACE_MD}px 0;"
        ):
            color = COLORS["primary"] if saved >= 0 else COLORS["error"]
            with ui.column().classes("gap-0"):
                ui.label("Tokens saved").style(label_style())
                with ui.row().classes("items-baseline gap-1"):
                    ui.label(f"{sign}{number_fmt(saved)}").style(
                        f"color: {color}; font-size: 44px; "
                        "font-weight: 800; line-height: 1;"
                    )
                    ui.label(f"({sign}{pct:.0f}%)").style(
                        f"color: {color}; font-size: {TEXT_LG}px; "
                        "font-weight: 600;"
                    )

            with ui.column().classes("gap-0"):
                ui.label("Estimated cost").style(label_style())
                with ui.row().classes("items-baseline gap-1"):
                    ui.label(quote.cost_str).style(
                        f"color: {color}; font-size: 32px; "
                        "font-weight: 700; line-height: 1;"
                    )
                    ui.label(f"@ {quote.model_label}").style(
                        f"color: {COLORS['text_muted']}; "
                        f"font-size: {TEXT_SM}px;"
                    )

            with ui.column().classes("gap-0"):
                ui.label("Interventions").style(label_style())
                ui.label(f"{n_intervene}").style(
                    f"color: {COLORS['accent']}; font-size: 32px; "
                    "font-weight: 700; line-height: 1;"
                )

        # Context line
        ui.label(
            f"Raw output sent by tools totaled {number_fmt(raw)} tokens; "
            f"after PlanckBot filtering Claude received "
            f"{number_fmt(filtered)} tokens."
        ).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "line-height: 1.5;"
        )

        # Per-tool breakdown
        if per_tool:
            ui.separator().style(f"margin: {SPACE_MD}px 0; "
                                 f"background: {COLORS['border']};")
            ui.label("By tool").style(label_style())
            max_saved = max(abs(raw_t - filt_t) for _, _, raw_t, filt_t in per_tool) or 1
            for tool, n, raw_t, filt_t in per_tool:
                saved_t = raw_t - filt_t
                pct_t = (saved_t / raw_t * 100) if raw_t else 0
                color_t = COLORS["primary"] if saved_t >= 0 else COLORS["error"]
                bar_pct = abs(saved_t) / max_saved * 100
                sign_t = "+" if saved_t >= 0 else ""

                cost_t = estimate_cost(saved_t, model_id=current)
                with ui.row().classes("w-full items-center gap-3 no-wrap").style(
                    f"padding: {SPACE_SM}px 0;"
                ):
                    ui.label(tool).style(
                        f"color: {COLORS['text']}; font-size: {TEXT_SM}px; "
                        "font-family: monospace; font-weight: 600; "
                        "flex: 0 0 200px;"
                    )
                    # Bar
                    with ui.column().classes("").style("flex: 1; min-width: 80px;"):
                        ui.element("div").style(
                            "width: 100%; height: 6px; border-radius: 3px; "
                            f"background: {COLORS['surface2']}; "
                            "position: relative; overflow: hidden;"
                        )
                        with ui.element("div").style(
                            "width: 100%; height: 6px; border-radius: 3px; "
                            f"background: {COLORS['surface2']}; "
                            "position: relative; overflow: hidden; "
                            "margin-top: -6px;"
                        ):
                            ui.element("div").style(
                                f"position: absolute; inset: 0; "
                                f"width: {bar_pct}%; "
                                f"background: {color_t}; "
                                "border-radius: 3px;"
                            )
                    ui.label(f"{sign_t}{number_fmt(saved_t)} tok").style(
                        f"color: {color_t}; font-size: {TEXT_SM}px; "
                        f"font-weight: 600; "
                        "font-variant-numeric: tabular-nums; "
                        "flex: 0 0 100px; text-align: right;"
                    )
                    ui.label(f"{sign_t}{pct_t:.0f}%").style(
                        f"color: {color_t}; font-size: {TEXT_SM}px; "
                        f"flex: 0 0 56px; text-align: right;"
                    )
                    ui.label(cost_t.cost_str).style(
                        f"color: {color_t}; font-size: {TEXT_SM}px; "
                        f"font-weight: 600; "
                        "flex: 0 0 80px; text-align: right;"
                    )
                    ui.label(f"{n} call{'s' if n != 1 else ''}").style(
                        f"color: {COLORS['text_muted']}; font-size: 11px; "
                        "flex: 0 0 60px; text-align: right;"
                    )


def _empty_savings(state) -> None:
    """Shown when intervene_count == 0 — no intervention has happened yet."""
    ui.label(
        "Nothing to save yet — PlanckBot only counts savings for "
        "`proxy:intervene` triples (where the adapter actually swapped "
        "the tool's output). To start seeing numbers here:"
    ).style(
        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
        "line-height: 1.6;"
    )
    steps = [
        ("Train or receive a blessed adapter", "planckbot train --tool X --fixture Y.json --activate"),
        ("Flip proxy mode to `intervene` in ~/.claude.json", "planckbot init --mode intervene --force"),
        ("Restart Claude Code", "close + reopen your Claude Code session"),
    ]
    with ui.column().classes("gap-2 w-full").style(f"margin-top: {SPACE_SM}px;"):
        for i, (title, cmd) in enumerate(steps):
            with ui.row().classes("items-start gap-2 w-full no-wrap").style(
                f"padding: {SPACE_SM}px 0;"
            ):
                ui.icon("chevron_right").style(
                    f"color: {COLORS['text_muted']}; font-size: 18px; "
                    "margin-top: 1px;"
                )
                with ui.column().classes("gap-0"):
                    ui.label(f"{i + 1}. {title}").style(
                        f"color: {COLORS['text']}; font-size: {TEXT_SM}px; "
                        "font-weight: 600;"
                    )
                    ui.label(cmd).style(
                        f"color: {COLORS['accent']}; font-size: 12px; "
                        "font-family: monospace;"
                    )
