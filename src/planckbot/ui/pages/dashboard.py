"""Dashboard: hero welcome, stats at a glance, recent activity."""

from __future__ import annotations

from nicegui import ui

from planckbot.ui.components import empty_state, page_header, stat_card, status_badge
from planckbot.ui.mascots import mascot_svg
from planckbot.ui.state import get_state
from planckbot.ui.theme import (
    COLORS,
    ELEVATED_CARD_STYLE,
    RADIUS_LG,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    TEXT_LG,
    TEXT_MD,
    TEXT_SM,
    WORDMARK_HTML,
    body_style,
    heading_style,
    label_style,
    number_fmt,
    number_style,
    relative_time,
    subtitle_style,
)


def _hero(state) -> None:
    """Top welcome panel — branded, shows user their workbench identity."""
    tool_count = len(state.registry.list_tools())
    triples = state.triples.count_total()
    ckpts = state.checkpoints.count()

    with ui.row().classes("w-full items-center no-wrap gap-6").style(
        f"margin-bottom: {SPACE_LG}px; "
        f"padding: {SPACE_LG}px {SPACE_LG}px; "
        f"border-radius: {RADIUS_LG}px; "
        f"background: linear-gradient(135deg, "
        f"rgba(95, 212, 163, 0.06) 0%, "
        f"rgba(126, 229, 214, 0.04) 40%, "
        f"transparent 100%), {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"box-shadow: 0 1px 0 {COLORS['accent']}14 inset, "
        "0 12px 40px rgba(0, 0, 0, 0.35);"
    ):
        ui.image("/branding/planckbots-mark-512.png").style(
            "width: 112px; height: 112px; border-radius: 50%; "
            "object-fit: cover; flex: 0 0 112px; "
            f"border: 1px solid {COLORS['border_glow']}; "
            f"box-shadow: 0 0 36px {COLORS['primary']}33, "
            f"0 0 4px {COLORS['accent']}66;"
        )
        with ui.column().classes("gap-1 flex-1"):
            ui.label("Welcome back").style(
                f"color: {COLORS['primary']}; "
                "font-size: 11px; font-weight: 700; "
                "letter-spacing: 1.5px; text-transform: uppercase;"
            )
            ui.html(WORDMARK_HTML).style("font-size: 34px; line-height: 1.1;")
            ui.label(
                "Adaptive tiny-model layer for LLM token optimization. "
                f"{number_fmt(triples)} triples observed across "
                f"{tool_count} tools — {ckpts} checkpoint"
                f"{'s' if ckpts != 1 else ''} trained."
            ).style(
                f"{subtitle_style()} max-width: 640px;"
            )


def _stats(state) -> None:
    exp_count = state.experiments.count()
    triple_count = state.triples.count_total()
    ckpt_count = state.checkpoints.count()
    tool_count = len(state.registry.list_tools())

    # Running experiments count for a delta hint
    running = len(state.experiments.list_all(status="running"))
    running_hint = f"{running} running" if running else "all idle"

    # Recent triple count (last 24h) — best-effort, fall back gracefully
    try:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        row = state.conn.execute(
            "SELECT COUNT(*) AS n FROM triples WHERE created_at >= ?", (cutoff,)
        ).fetchone()
        recent_triples = row["n"] if row else 0
    except Exception:
        recent_triples = 0
    triples_hint = f"+{number_fmt(recent_triples)} in last 24h"

    # Token savings: only counts triples where proxy actually intervened
    savings = state.triples.token_savings(source="proxy:intervene")
    saved = savings["saved"]
    n_intervene = savings["intervene_count"]
    if n_intervene == 0:
        savings_value = "—"
        savings_hint = "no interventions yet"
        savings_tint = "brass"
    else:
        # Show with sign so regressions are obvious
        prefix = "+" if saved >= 0 else ""
        savings_value = f"{prefix}{number_fmt(saved)}"
        pct = (saved / savings["raw_tokens"] * 100) if savings["raw_tokens"] else 0
        savings_hint = f"{prefix}{pct:.0f}% over {n_intervene} call{'s' if n_intervene != 1 else ''}"
        savings_tint = "primary" if saved > 0 else "error"

    with ui.row().classes("w-full gap-4 flex-wrap").style(
        f"margin-bottom: {SPACE_LG}px;"
    ):
        stat_card("Tokens saved", savings_value, delta=savings_hint,
                  icon="savings", tint=savings_tint)
        stat_card("Experiments", exp_count, delta=running_hint,
                  icon="science", tint="primary")
        stat_card("Triples", triple_count, delta=triples_hint,
                  icon="data_object", tint="accent")
        stat_card("Checkpoints", ckpt_count,
                  delta="adapters on disk" if ckpt_count else "train one to see it here",
                  icon="save", tint="brass")
        stat_card("Tools", tool_count,
                  delta="registered in registry",
                  icon="build", tint="info")


def _recent_experiments(state) -> None:
    with ui.column().classes("flex-1 gap-3").style("min-width: 520px;"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Recent experiments").style(heading_style(size=TEXT_LG))
            ui.link("See all →", "/experiments").classes("no-underline").style(
                f"color: {COLORS['primary']}; font-size: {TEXT_SM}px; font-weight: 600;"
            )

        recent = state.experiments.list_all(limit=8)
        if not recent:
            empty_state(
                title="No experiments yet",
                hint="Run your first training experiment to see it appear here.",
                icon="science",
            )
            return

        with ui.column().classes("w-full gap-0").style(
            f"background: {COLORS['surface']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: {RADIUS_LG}px; overflow: hidden;"
        ):
            for i, e in enumerate(recent):
                border_top = (
                    "" if i == 0 else f"border-top: 1px solid {COLORS['border']};"
                )
                with ui.row().classes(
                    "w-full items-center justify-between no-wrap gap-3"
                ).style(
                    f"padding: {SPACE_MD}px {SPACE_LG}px; {border_top}"
                ):
                    with ui.column().classes("gap-0 flex-1").style("min-width: 0;"):
                        ui.label(e.name or "(unnamed)").style(
                            f"color: {COLORS['text']}; "
                            f"font-size: {TEXT_MD}px; font-weight: 600; "
                            "overflow: hidden; text-overflow: ellipsis; "
                            "white-space: nowrap;"
                        )
                        ui.label(
                            f"{e.experiment_type} · {relative_time(e.created_at)}"
                        ).style(
                            f"color: {COLORS['text_muted']}; "
                            f"font-size: {TEXT_SM}px;"
                        )
                    status_badge(e.status)


def _tool_activity(state) -> None:
    counts = state.triples.count_by_tool()
    if not counts:
        return

    with ui.column().classes("gap-3").style("min-width: 320px;"):
        ui.label("Tool activity").style(heading_style(size=TEXT_LG))

        max_count = max(counts.values())
        sorted_tools = sorted(counts.items(), key=lambda x: -x[1])[:8]

        with ui.column().classes("w-full gap-0").style(
            f"background: {COLORS['surface']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: {RADIUS_LG}px; overflow: hidden;"
        ):
            for i, (tool, cnt) in enumerate(sorted_tools):
                border_top = (
                    "" if i == 0 else f"border-top: 1px solid {COLORS['border']};"
                )
                pct = (cnt / max_count) * 100 if max_count else 0
                with ui.row().classes(
                    "w-full items-center no-wrap gap-3"
                ).style(
                    f"padding: {SPACE_MD}px {SPACE_LG}px; {border_top}"
                ):
                    ui.html(mascot_svg(tool, size=40)).style("flex: 0 0 40px;")
                    with ui.column().classes("gap-1 flex-1").style("min-width: 0;"):
                        with ui.row().classes(
                            "w-full items-center justify-between no-wrap"
                        ):
                            ui.label(tool).style(
                                f"color: {COLORS['text']}; "
                                f"font-size: {TEXT_MD}px; font-weight: 600;"
                            )
                            ui.label(number_fmt(cnt)).style(
                                f"color: {COLORS['primary']}; "
                                f"font-size: {TEXT_MD}px; font-weight: 700; "
                                "font-variant-numeric: tabular-nums;"
                            )
                        # progress bar
                        ui.element("div").style(
                            "width: 100%; height: 4px; border-radius: 2px; "
                            f"background: {COLORS['surface2']}; "
                            f"position: relative; overflow: hidden;"
                        ).tooltip(f"{cnt} triples").classes("planck-progress").props(
                            f'data-pct="{pct:.0f}"'
                        )
                        # inner bar (rendered via nested element)
                        with ui.element("div").style(
                            "width: 100%; height: 4px; border-radius: 2px; "
                            f"background: {COLORS['surface2']}; "
                            f"position: relative; overflow: hidden; "
                            f"margin-top: -4px;"
                        ):
                            ui.element("div").style(
                                f"position: absolute; inset: 0; "
                                f"width: {pct}%; "
                                f"background: linear-gradient(90deg, "
                                f"{COLORS['primary']} 0%, {COLORS['accent']} 100%); "
                                f"border-radius: 2px;"
                            )


def _system_info(state) -> None:
    info = state.config.system_info
    with ui.row().classes("w-full gap-3 items-stretch flex-wrap").style(
        f"margin-top: {SPACE_LG}px;"
    ):
        for key, val in info.items():
            with ui.column().classes("gap-1").style(
                f"min-width: 140px; flex: 1; "
                f"padding: {SPACE_MD}px {SPACE_LG}px; "
                f"background: {COLORS['surface']}; "
                f"border: 1px solid {COLORS['border']}; "
                f"border-radius: {RADIUS_LG}px;"
            ):
                ui.label(key.replace("_", " ")).style(label_style())
                ui.label(str(val)).style(
                    f"color: {COLORS['text']}; "
                    f"font-size: {TEXT_MD}px; font-weight: 600; "
                    "font-variant-numeric: tabular-nums;"
                )


def dashboard_page():
    state = get_state()

    _hero(state)
    _stats(state)

    with ui.row().classes("w-full gap-4 flex-wrap items-start"):
        _recent_experiments(state)
        _tool_activity(state)

    ui.label("System").style(
        label_style() + f" margin-top: {SPACE_LG}px;"
    )
    _system_info(state)
