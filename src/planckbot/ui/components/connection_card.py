"""Connection card: tells the user where PlanckBot is currently watching,
in what mode, and whether the MCP subprocesses are alive."""

from __future__ import annotations

from nicegui import ui

from planckbot.ui.mcp_status import MCPStatus, read_mcp_status
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
)


def _mode_color(mode: str | None) -> str:
    return {
        "observe": COLORS["info"],
        "suggest": COLORS["warning"],
        "intervene": COLORS["primary"],
    }.get(mode or "", COLORS["text_muted"])


def _health_icon_color(healthy: bool) -> str:
    return COLORS["success"] if healthy else COLORS["warning"]


def _row(label: str, value: str, *, value_color: str | None = None,
         value_mono: bool = False) -> None:
    with ui.row().classes("w-full items-center justify-between no-wrap").style(
        f"padding: {SPACE_SM}px 0; "
        f"border-bottom: 1px dashed {COLORS['border']};"
    ):
        ui.label(label).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "letter-spacing: 0.3px;"
        )
        style = (
            f"color: {value_color or COLORS['text']}; "
            f"font-size: {TEXT_SM}px; font-weight: 600; "
            f"{'font-family: monospace;' if value_mono else ''}"
        )
        ui.label(value).style(style)


def connection_card() -> MCPStatus:
    """Render the card and return the status so the caller can branch on it."""
    st = read_mcp_status()

    # Outer shell
    with ui.column().classes("w-full gap-1").style(
        f"margin-bottom: {SPACE_LG}px; "
        f"padding: {SPACE_LG}px; "
        f"background: {COLORS['surface']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px;"
    ):
        # Header row — title + overall health pill
        with ui.row().classes("w-full items-center justify-between no-wrap").style(
            f"margin-bottom: {SPACE_SM}px;"
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("cable").style(
                    f"color: {COLORS['primary']}; font-size: 20px;"
                )
                ui.label("Connection").style(heading_style(size=TEXT_LG))
            _connection_pill(st)

        if st.config_error:
            _error_body(st)
            return st

        if not st.configured:
            _unconfigured_body()
            return st

        # Configured — show details
        _row(
            "Watching",
            st.upstream_path or "(unknown)",
            value_mono=True,
        )
        _row(
            "Proxy mode",
            (st.mode or "observe"),
            value_color=_mode_color(st.mode),
        )
        if st.strategy:
            _row("Strategy", st.strategy, value_mono=True)
        if st.threshold is not None:
            _row("Confidence threshold", f"{st.threshold:.2f}")
        _row(
            "planckbot-fs",
            f"{'running' if st.fs_running else 'idle'} "
            f"({len(st.fs_pids)} proc)",
            value_color=_health_icon_color(st.fs_running),
        )
        _row(
            "planckbot-synth",
            "configured · "
            + (f"{len(st.synth_pids)} proc running"
               if st.synth_configured and st.synth_running
               else "not running" if st.synth_configured
               else "not configured"),
            value_color=(
                _health_icon_color(st.synth_healthy)
                if st.synth_configured else COLORS["text_muted"]
            ),
        )

        # Footer explanation line
        _explain(st)

    return st


def _connection_pill(st: MCPStatus) -> None:
    if st.config_error:
        txt, color = "config invalid", COLORS["error"]
    elif not st.configured:
        txt, color = "not connected", COLORS["error"]
    elif not st.fs_running:
        txt, color = "idle", COLORS["warning"]
    else:
        txt, color = f"live · {st.mode or 'observe'}", COLORS["success"]

    ui.html(
        f'<span style="display:inline-flex; align-items:center; gap:6px; '
        f'padding:4px 10px; border-radius:999px; font-size:11px; '
        f'font-weight:700; letter-spacing:0.5px; text-transform:uppercase; '
        f'color:{color}; background:{color}18; border:1px solid {color}55;">'
        f'<span style="width:7px; height:7px; border-radius:50%; '
        f'background:{color}; box-shadow:0 0 6px {color};"></span>'
        f'{txt}</span>'
    )


def _unconfigured_body() -> None:
    ui.label(
        "PlanckBot is installed but Claude Code isn't wired to it yet. "
        "Run `planckbot init` to register the MCP servers, then restart "
        "Claude Code."
    ).style(
        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
        "line-height: 1.5;"
    )
    with ui.row().classes("items-center gap-2").style(
        f"margin-top: {SPACE_MD}px;"
    ):
        ui.html(
            f'<code style="background: {COLORS["surface2"]}; '
            f'color: {COLORS["accent"]}; padding: 4px 10px; '
            f'border-radius: 6px; font-size: {TEXT_SM}px; '
            f'font-family: monospace;">planckbot init --upstream-path '
            f'/abs/path/to/your/project</code>'
        )


def _error_body(st: MCPStatus) -> None:
    ui.label(
        f"Could not read ~/.claude.json: {st.config_error}. "
        "Fix the JSON and PlanckBot will auto-detect the connection."
    ).style(
        f"color: {COLORS['error']}; font-size: {TEXT_SM}px; "
        "line-height: 1.5;"
    )


def _explain(st: MCPStatus) -> None:
    if st.mode == "observe":
        msg = (
            "In `observe` mode every MCP tool call is recorded as a triple, "
            "but nothing is filtered. Flip to `suggest` to see adapter "
            "predictions alongside raw output; to `intervene` once you "
            "trust the adapter."
        )
    elif st.mode == "suggest":
        msg = (
            "In `suggest` mode the adapter runs on every call and its "
            "prediction is stored, but Claude still sees raw output. "
            "Good for evaluating before intervening."
        )
    elif st.mode == "intervene":
        msg = (
            "In `intervene` mode the adapter's filtered output is returned "
            "to Claude when its confidence exceeds the threshold. Token "
            "savings accumulate here."
        )
    else:
        msg = "Proxy mode not set — assuming `observe`."
    ui.label(msg).style(
        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
        f"line-height: 1.5; margin-top: {SPACE_MD}px; "
        "font-style: italic;"
    )
