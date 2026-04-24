"""Dashboard: state-aware hero, stats, onboarding card, recent activity."""

from __future__ import annotations

from nicegui import ui

from planckbot.ui.components import empty_state, page_header, stat_card, status_badge
from planckbot.ui.components.connection_card import connection_card
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


def _detect_state(state) -> dict:
    """Classify the install into one of four buckets so the hero can speak
    to the user's actual situation rather than generic marketing copy."""
    triples_total = state.triples.count_total()
    conn = state.conn
    proxy_triples = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE source LIKE 'proxy:%'"
    ).fetchone()[0]
    labeled = conn.execute(
        "SELECT COUNT(*) FROM triples WHERE filtered_output IS NOT NULL"
    ).fetchone()[0]
    checkpoints = state.checkpoints.count()
    active_ckpts = conn.execute(
        "SELECT COUNT(*) FROM model_checkpoints WHERE is_active = 1"
    ).fetchone()[0]
    savings = state.triples.token_savings(source="proxy:intervene")

    if triples_total == 0:
        bucket = "setup"
    elif proxy_triples == 0:
        bucket = "fixtures_only"  # only manual/fixture data
    elif checkpoints == 0:
        bucket = "collecting"
    elif active_ckpts == 0:
        bucket = "trained"
    elif savings["intervene_count"] == 0:
        bucket = "ready_to_intervene"
    else:
        bucket = "intervening"

    return {
        "bucket": bucket,
        "triples_total": triples_total,
        "proxy_triples": proxy_triples,
        "labeled": labeled,
        "checkpoints": checkpoints,
        "active_ckpts": active_ckpts,
        "savings": savings,
    }


def _hero(state) -> None:
    """State-aware top panel. The headline and the body copy both change
    based on where the install currently is in the funnel."""
    ctx = _detect_state(state)
    bucket = ctx["bucket"]

    # Headline + body copy per bucket.
    if bucket == "setup":
        eyebrow = "Let's get you set up"
        body = (
            "Point Claude Code at the PlanckBot MCP proxy. Every filesystem "
            "tool call will then land here as a triple, and the system starts "
            "learning from your workflow."
        )
    elif bucket == "fixtures_only":
        eyebrow = "Waiting for real traffic"
        body = (
            f"You have {ctx['triples_total']} triples from fixtures, but "
            "nothing from Claude yet. Restart Claude Code after `planckbot "
            "init` and try a prompt that uses `mcp__planckbot-fs__*`."
        )
    elif bucket == "collecting":
        eyebrow = "Collecting data"
        body = (
            f"{ctx['proxy_triples']} tool calls observed so far; "
            f"{ctx['labeled']} auto-labeled. When a tool hits ~200 labeled "
            "triples, train your first adapter."
        )
    elif bucket == "trained":
        eyebrow = "Adapters ready"
        body = (
            f"You have {ctx['checkpoints']} checkpoint(s) trained but none "
            "active. Activate one from /models, then flip the proxy mode to "
            "`suggest` to see predictions against real traffic."
        )
    elif bucket == "ready_to_intervene":
        eyebrow = "Everything's armed"
        body = (
            f"Active adapter loaded. Flip the proxy mode to `intervene` in "
            "~/.claude.json and restart Claude Code — from there savings "
            "accumulate automatically on every filesystem tool call."
        )
    else:  # intervening
        saved = ctx["savings"]["saved"]
        pct = (saved / ctx["savings"]["raw_tokens"] * 100
               if ctx["savings"]["raw_tokens"] else 0)
        sign = "+" if saved >= 0 else ""
        eyebrow = "Saving tokens"
        body = (
            f"{sign}{number_fmt(saved)} tokens across "
            f"{ctx['savings']['intervene_count']} interventions "
            f"({sign}{pct:.0f}% of raw). Keep using Claude — "
            "more supervision → better adapter → more savings."
        )

    with ui.row().classes("w-full items-center no-wrap gap-6").style(
        f"margin-bottom: {SPACE_LG}px; "
        f"padding: {SPACE_LG}px {SPACE_LG}px; "
        f"border-radius: {RADIUS_LG}px; "
        f"background: linear-gradient(135deg, "
        f"rgba(95, 212, 163, 0.07) 0%, "
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
            ui.label(eyebrow.upper()).style(
                f"color: {COLORS['primary']}; "
                "font-size: 11px; font-weight: 700; "
                "letter-spacing: 1.5px;"
            )
            ui.html(WORDMARK_HTML).style("font-size: 34px; line-height: 1.1;")
            ui.label(body).style(subtitle_style() + " max-width: 640px;")


def _onboarding_card(state) -> None:
    """Only shown in the 'setup' and 'fixtures_only' buckets — a visible
    checklist of the three first steps. Disappears once traffic starts
    flowing."""
    ctx = _detect_state(state)
    if ctx["bucket"] not in {"setup", "fixtures_only"}:
        return

    steps = [
        (
            True,  # install is done — we wouldn't be rendering if not
            "Install PlanckBot",
            "pip install -e \".[dev]\" · done",
        ),
        (
            ctx["proxy_triples"] > 0,
            "Register the MCP server",
            "run `planckbot init` and restart Claude Code",
        ),
        (
            ctx["proxy_triples"] > 0,
            "Let Claude make a tool call",
            "any mcp__planckbot-fs__* call becomes your first real triple",
        ),
    ]

    with ui.column().classes("w-full gap-2").style(
        f"margin-bottom: {SPACE_LG}px; "
        f"padding: {SPACE_LG}px; "
        f"background: {COLORS['surface']}; "
        f"border: 1px dashed {COLORS['primary']}55; "
        f"border-radius: {RADIUS_LG}px;"
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon("rocket_launch").style(
                f"color: {COLORS['primary']}; font-size: 22px;"
            )
            ui.label("First three steps").style(heading_style(size=TEXT_LG))
        ui.label(
            "Get from zero to your first real triple. The card disappears "
            "once the MCP starts recording."
        ).style(f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px;")
        for i, (done, title, detail) in enumerate(steps):
            with ui.row().classes("items-start gap-3 w-full no-wrap").style(
                f"padding: {SPACE_SM}px 0; "
                f"border-top: 1px solid {COLORS['border']};"
                if i > 0 else f"padding: {SPACE_SM}px 0;"
            ):
                if done:
                    ui.icon("check_circle").style(
                        f"color: {COLORS['success']}; font-size: 22px; "
                        "margin-top: 1px;"
                    )
                else:
                    ui.icon("radio_button_unchecked").style(
                        f"color: {COLORS['text_muted']}; font-size: 22px; "
                        "margin-top: 1px;"
                    )
                with ui.column().classes("gap-0").style("flex: 1;"):
                    ui.label(f"Step {i+1}. {title}").style(
                        f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
                        "font-weight: 600;"
                    )
                    ui.label(detail).style(
                        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                        "font-family: monospace;"
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
    _onboarding_card(state)   # only shows when install is empty / fixtures-only
    connection_card()         # where is PlanckBot watching, in what mode
    _stats(state)

    with ui.row().classes("w-full gap-4 flex-wrap items-start"):
        _recent_experiments(state)
        _tool_activity(state)

    ui.label("System").style(
        label_style() + f" margin-top: {SPACE_LG}px;"
    )
    _system_info(state)
