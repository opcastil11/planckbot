"""Visual onboarding page: how PlanckBot works, end to end.

Takes one vertical scroll to read. Each section is a diagram + a few lines
of plain text. Real data (a live triple, savings counters) is pulled from
the DB so the page is specific to this user's install, not a marketing
screenshot. Non-live (no timer) — the other pages cover live dashboards.
"""

from __future__ import annotations

from nicegui import ui

from planckbot.experiments.metrics import count_tokens_approx
from planckbot.ui.components.page_header import page_header
from planckbot.ui.state import get_state
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
    subtitle_style,
)


# --- little reusable visual primitives -------------------------------------


def _card(bg=None, border=None, glow=False):
    """Context manager for a themed card. Returns the ui.element."""
    style = (
        f"background: {bg or COLORS['surface']}; "
        f"border: 1px solid {border or COLORS['border']}; "
        f"border-radius: {RADIUS_LG}px; "
        f"padding: {SPACE_LG}px {SPACE_LG}px; "
    )
    if glow:
        style += (
            f"box-shadow: 0 0 24px {COLORS['primary']}22, "
            f"inset 0 1px 0 {COLORS['accent']}11;"
        )
    return ui.column().classes("w-full").style(style)


def _pill(text: str, color: str = None, bg: str = None) -> None:
    c = color or COLORS["primary"]
    b = bg or f"{COLORS['primary']}18"
    ui.html(
        f'<span style="display:inline-block; padding: 3px 10px; '
        f'border-radius: 999px; font-size: 11px; font-weight: 600; '
        f'color: {c}; background: {b}; '
        f'border: 1px solid {c}44; letter-spacing: 0.4px;'
        f'">{text}</span>'
    )


def _arrow_right():
    """Small styled arrow cell between flow boxes."""
    ui.html(
        f'<div style="display:flex; align-items:center; justify-content:center; '
        f'padding: 0 8px; color: {COLORS["text_muted"]}; font-size: 22px;">→</div>'
    )


def _flow_box(title: str, subtitle: str, tint: str = "primary"):
    color = COLORS.get(tint, COLORS["primary"])
    with ui.column().classes("items-center gap-1").style(
        f"background: {COLORS['surface2']}; "
        f"border: 1px solid {color}44; "
        f"border-radius: 10px; "
        f"padding: {SPACE_MD}px {SPACE_LG}px; "
        f"min-width: 140px; max-width: 200px;"
    ):
        ui.label(title).style(
            f"color: {color}; font-size: {TEXT_MD}px; font-weight: 700; "
            "text-align: center; font-family: monospace;"
        )
        ui.label(subtitle).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            "text-align: center; line-height: 1.3;"
        )


def _big_number(value: str, unit: str, color: str, caption: str = "") -> None:
    with ui.column().classes("items-center gap-1").style("min-width: 140px;"):
        with ui.row().classes("items-baseline gap-1"):
            ui.label(value).style(
                f"color: {color}; font-size: 42px; font-weight: 800; "
                "font-variant-numeric: tabular-nums; line-height: 1;"
            )
            ui.label(unit).style(
                f"color: {COLORS['text_muted']}; "
                f"font-size: {TEXT_MD}px; font-weight: 600;"
            )
        if caption:
            ui.label(caption).style(
                f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                "letter-spacing: 1px; text-transform: uppercase;"
            )


def _token_bar(label: str, tokens: int, max_tokens: int, color: str) -> None:
    """Horizontal bar showing token count relative to a ceiling."""
    pct = (tokens / max_tokens * 100) if max_tokens else 0
    with ui.column().classes("gap-1 w-full"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(label).style(
                f"color: {COLORS['text']}; font-size: {TEXT_SM}px; "
                "font-weight: 600;"
            )
            ui.label(f"{number_fmt(tokens)} tok").style(
                f"color: {color}; font-size: {TEXT_MD}px; font-weight: 700; "
                "font-variant-numeric: tabular-nums;"
            )
        ui.element("div").style(
            "width: 100%; height: 10px; border-radius: 5px; "
            f"background: {COLORS['surface2']}; position: relative; overflow: hidden;"
        ).props(f'data-pct="{pct:.0f}"')
        with ui.element("div").style(
            "width: 100%; height: 10px; border-radius: 5px; "
            f"background: {COLORS['surface2']}; position: relative; overflow: hidden; "
            "margin-top: -10px;"
        ):
            ui.element("div").style(
                f"position: absolute; inset: 0; width: {pct}%; "
                f"background: linear-gradient(90deg, {color}dd, {color}); "
                "border-radius: 5px;"
            )


# --- sections --------------------------------------------------------------


def _hero(state) -> None:
    savings = state.triples.token_savings(source="proxy:intervene")
    triples_total = state.triples.count_total()
    ckpts = state.checkpoints.count()

    with _card(glow=True):
        with ui.row().classes("w-full items-center gap-6 flex-wrap"):
            with ui.column().classes("gap-2").style("flex: 2; min-width: 300px;"):
                ui.label("HOW IT WORKS").style(
                    f"color: {COLORS['primary']}; font-size: 11px; "
                    "font-weight: 700; letter-spacing: 2px;"
                )
                ui.label(
                    "A thin layer between Claude and its tools"
                ).style(
                    f"color: {COLORS['text']}; font-size: 28px; "
                    "font-weight: 700; line-height: 1.2;"
                )
                ui.label(
                    "PlanckBot sits on the wire between the host LLM (that's "
                    "you, Claude) and the tools it calls. It observes every "
                    "call, trains per-tool tiny models to compress outputs, "
                    "and over time synthesizes brand-new tools from usage "
                    "patterns."
                ).style(subtitle_style() + " max-width: 560px;")

            # Stats
            with ui.row().classes("gap-6 items-center").style(
                "flex: 1; min-width: 260px; justify-content: flex-end;"
            ):
                _big_number(
                    number_fmt(triples_total), "triples",
                    COLORS["accent"], "observed",
                )
                _big_number(
                    str(ckpts), "adapters",
                    COLORS["brass"], "trained",
                )
                saved = savings["saved"]
                pct = (saved / savings["raw_tokens"] * 100) if savings["raw_tokens"] else 0
                _big_number(
                    f"{'+' if saved >= 0 else ''}{pct:.0f}%",
                    "saved",
                    COLORS["primary"] if saved >= 0 else COLORS["error"],
                    f"{savings['intervene_count']} interventions",
                )


def _section_routes() -> None:
    ui.label("1 · The four routes Claude can take").style(
        heading_style(size=TEXT_LG)
    )
    ui.label(
        "When Claude emits a tool_use, it lands in one of four places. "
        "PlanckBot only sees the middle two — the other routes are "
        "invisible to us."
    ).style(subtitle_style())

    with _card():
        with ui.row().classes("w-full items-stretch gap-2 flex-wrap"):
            with ui.column().classes("items-center justify-center").style(
                "min-width: 160px;"
            ):
                _flow_box(
                    "Claude",
                    "decides which tool",
                    tint="secondary",
                )

            ui.html(
                f'<div style="display:flex; align-items:center; '
                f'color: {COLORS["text_muted"]}; font-size: 28px;">→</div>'
            )

            with ui.column().classes("gap-3").style("flex: 1;"):
                with ui.row().classes("items-center gap-3 w-full"):
                    _flow_box(
                        "Native tools",
                        "Read, LS, Grep, Bash",
                        tint="text_muted",
                    )
                    ui.label("invisible — built into Claude Code, PlanckBot never sees these").style(
                        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                        "font-style: italic;"
                    )

                with ui.row().classes("items-center gap-3 w-full"):
                    _flow_box(
                        "mcp__planckbot-fs__*",
                        "filesystem MCP proxy",
                        tint="primary",
                    )
                    ui.label(
                        "★ intercepted — proxy records the triple and "
                        "optionally filters output with a tiny LLM"
                    ).style(
                        f"color: {COLORS['text']}; font-size: {TEXT_SM}px;"
                    )

                with ui.row().classes("items-center gap-3 w-full"):
                    _flow_box(
                        "mcp__planckbot-synth__*",
                        "synthesized tools",
                        tint="brass",
                    )
                    ui.label(
                        "★ synthesized Python scripts — generated from "
                        "usage patterns, no LLM at runtime"
                    ).style(
                        f"color: {COLORS['text']}; font-size: {TEXT_SM}px;"
                    )

                with ui.row().classes("items-center gap-3 w-full"):
                    _flow_box(
                        "Other MCPs",
                        "Gmail, Drive, ...",
                        tint="text_muted",
                    )
                    ui.label(
                        "invisible — PlanckBot only proxies the planckbot-* "
                        "MCP servers"
                    ).style(
                        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                        "font-style: italic;"
                    )


def _section_triple(state) -> None:
    ui.label("2 · The unit of data — a triple").style(
        heading_style(size=TEXT_LG)
    )
    ui.label(
        "Every MCP call that reaches PlanckBot becomes a row in the DB: "
        "its INPUT, the tool's raw OUTPUT, and (when the auto-label loop "
        "fills it in) the subset Claude actually cited afterwards — the "
        "FILTERED_OUTPUT. That third piece is the supervision signal the "
        "tiny LLM trains on."
    ).style(subtitle_style())

    # Pull a real labeled triple if we have one; otherwise show a fallback.
    triple = _pick_illustrative_triple(state)
    raw_tok = triple.output_tokens or count_tokens_approx(triple.output_data or "")
    filt_tok = triple.filtered_tokens or 0

    with _card():
        with ui.row().classes("w-full items-stretch gap-4 flex-wrap"):
            # INPUT
            with ui.column().classes("gap-2").style(
                f"flex: 1; min-width: 240px; "
                f"padding: {SPACE_MD}px; "
                f"background: {COLORS['surface2']}; "
                f"border-radius: 10px; border: 1px solid {COLORS['border']};"
            ):
                _pill("INPUT", color=COLORS["info"])
                ui.label("what Claude sent").style(label_style())
                ui.code(
                    (triple.input_data or "")[:200] + (
                        "..." if len(triple.input_data or "") > 200 else ""
                    )
                ).classes("text-xs").style(
                    f"background: {COLORS['bg']}; font-size: 12px; "
                    "max-height: 120px; overflow: auto; word-break: break-all;"
                )
                ui.label(f"{triple.input_tokens or 0} tokens").style(
                    f"color: {COLORS['info']}; font-size: {TEXT_SM}px; "
                    "font-weight: 700;"
                )

            ui.html(
                f'<div style="display:flex; align-items:center; '
                f'color: {COLORS["text_muted"]}; font-size: 28px;">→</div>'
            )

            # RAW OUTPUT
            with ui.column().classes("gap-2").style(
                f"flex: 2; min-width: 280px; "
                f"padding: {SPACE_MD}px; "
                f"background: {COLORS['surface2']}; "
                f"border-radius: 10px; border: 1px solid {COLORS['border']};"
            ):
                _pill("RAW OUTPUT", color=COLORS["warning"])
                ui.label("what the tool returned").style(label_style())
                ui.code(
                    (triple.output_data or "")[:350] + (
                        "..." if len(triple.output_data or "") > 350 else ""
                    )
                ).classes("text-xs").style(
                    f"background: {COLORS['bg']}; font-size: 12px; "
                    "max-height: 180px; overflow: auto; white-space: pre-wrap;"
                )
                ui.label(f"{raw_tok} tokens").style(
                    f"color: {COLORS['warning']}; font-size: {TEXT_SM}px; "
                    "font-weight: 700;"
                )

            ui.html(
                f'<div style="display:flex; align-items:center; '
                f'color: {COLORS["text_muted"]}; font-size: 28px;">→</div>'
            )

            # FILTERED OUTPUT
            with ui.column().classes("gap-2").style(
                f"flex: 1; min-width: 220px; "
                f"padding: {SPACE_MD}px; "
                f"background: {COLORS['primary']}08; "
                f"border-radius: 10px; "
                f"border: 1px solid {COLORS['primary']}66;"
            ):
                _pill("FILTERED", color=COLORS["primary"])
                ui.label("what was actually useful").style(label_style())
                if triple.filtered_output:
                    ui.code(triple.filtered_output[:200]).classes("text-xs").style(
                        f"background: {COLORS['bg']}; font-size: 12px; "
                        "max-height: 140px; overflow: auto; white-space: pre-wrap;"
                    )
                    ui.label(f"{filt_tok} tokens").style(
                        f"color: {COLORS['primary']}; font-size: {TEXT_SM}px; "
                        "font-weight: 700;"
                    )
                    saved_pct = (1 - filt_tok / raw_tok) * 100 if raw_tok else 0
                    ui.label(f"{saved_pct:.0f}% smaller").style(
                        f"color: {COLORS['primary']}; font-size: {TEXT_LG}px; "
                        "font-weight: 800;"
                    )
                else:
                    ui.label("(not yet labeled)").style(
                        f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                        "font-style: italic;"
                    )

        if triple.filtered_output:
            ui.separator().style(f"margin: {SPACE_MD}px 0; "
                                 f"background: {COLORS['border']};")
            ui.label(
                "Visual token budget:"
            ).style(label_style())
            with ui.column().classes("gap-2 w-full").style(
                f"padding: {SPACE_SM}px 0;"
            ):
                _token_bar("Raw", raw_tok, max(raw_tok, filt_tok),
                           COLORS["warning"])
                _token_bar("Filtered", filt_tok, max(raw_tok, filt_tok),
                           COLORS["primary"])


def _section_proxy_modes() -> None:
    ui.label("3 · The three proxy modes").style(heading_style(size=TEXT_LG))
    ui.label(
        "When Claude calls a proxied tool, PlanckBot decides what to do with "
        "the output. The mode is a per-MCP-server setting in ~/.claude.json."
    ).style(subtitle_style())

    modes = [
        (
            "observe",
            "Log only",
            "Claude sees the raw upstream output. PlanckBot just records the "
            "triple. Zero risk. Current default.",
            COLORS["info"],
            ["tool_use", "proxy records triple", "raw output to Claude"],
        ),
        (
            "suggest",
            "Log + predict, don't swap",
            "Same as observe, PLUS the tiny LLM makes a prediction and that "
            "prediction is saved alongside the triple. Claude still sees "
            "raw. Useful to eyeball if an adapter is any good before "
            "trusting it.",
            COLORS["warning"],
            ["tool_use", "proxy records + tiny LLM predicts", "raw output to Claude"],
        ),
        (
            "intervene",
            "Actually swap output",
            "If the tiny LLM's confidence ≥ threshold (default 0.9), Claude "
            "receives the FILTERED output instead of raw. This is where "
            "tokens get saved. Also where a bad adapter hurts — don't "
            "enable until you trust it.",
            COLORS["primary"],
            ["tool_use", "proxy records + tiny LLM filters", "FILTERED output to Claude"],
        ),
    ]

    with _card():
        for name, tagline, body, color, flow in modes:
            with ui.column().classes("w-full gap-2").style(
                f"padding: {SPACE_MD}px 0; "
                f"border-bottom: 1px solid {COLORS['border']};"
            ):
                with ui.row().classes("items-baseline gap-3"):
                    ui.label(name).style(
                        f"color: {color}; font-size: {TEXT_LG}px; "
                        f"font-weight: 800; font-family: monospace;"
                    )
                    ui.label("—").style(f"color: {COLORS['text_muted']};")
                    ui.label(tagline).style(
                        f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
                        "font-weight: 600;"
                    )
                ui.label(body).style(
                    f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                    "line-height: 1.5; max-width: 800px;"
                )
                with ui.row().classes("items-center gap-1 flex-wrap").style(
                    f"margin-top: {SPACE_SM}px;"
                ):
                    for i, step in enumerate(flow):
                        with ui.element("div").style(
                            f"padding: 4px 10px; "
                            f"background: {color}14; "
                            f"color: {color}; "
                            f"border: 1px solid {color}44; "
                            f"border-radius: 6px; "
                            f"font-size: 12px; font-family: monospace;"
                        ):
                            ui.label(step)
                        if i < len(flow) - 1:
                            ui.html(
                                f'<span style="color: {color}88; font-size: 14px;">→</span>'
                            )


def _section_layers() -> None:
    ui.label("4 · The three layers of adaptation").style(
        heading_style(size=TEXT_LG)
    )
    ui.label(
        "All three run on the same data (observed triples) but change the "
        "system at different levels. Together they are what makes PlanckBot "
        "more than just \"a tool observer\"."
    ).style(subtitle_style())

    layers = [
        (
            "Layer B",
            "Filter output at runtime",
            COLORS["primary"],
            "The tiny LLM (SmolLM2 + LoRA) learns per-tool. At call time it "
            "takes (input, output_bruto) and predicts filtered_output. "
            "What you see in the 97% savings screenshot of section 2 IS "
            "Layer B in action.",
            "mcp__planckbot-fs__list_directory — proxy + tiny LLM",
        ),
        (
            "Layer C",
            "Edit the tool itself",
            COLORS["brass"],
            "edit_tool(name, patch) rewrites the Python source of a "
            "registered tool (Layer-B adapters get invalidated because "
            "they were trained on the old version). AST whitelist blocks "
            "eval/exec/subprocess. The BIG LLM writes the patch; "
            "PlanckBot validates + versions + reloads.",
            "planckbot.tools.meta.edit_tool(...)",
        ),
        (
            "Layer D",
            "Synthesize new tools",
            COLORS["secondary"],
            "detect_tool_gaps scans triples for N-gram sequences that "
            "repeat (\"claude always calls list_directory then 3 "
            "read_files — there's a merged tool missing\"). The gap report "
            "becomes a synthesize_tool() call: the new tool ships through "
            "a dedicated MCP server (planckbot-synth).",
            "planckbot-synth MCP server",
        ),
    ]

    with ui.row().classes("w-full gap-3 flex-wrap"):
        for name, title, color, body, where in layers:
            with ui.column().classes("gap-2").style(
                f"flex: 1; min-width: 260px; "
                f"background: {COLORS['surface']}; "
                f"border: 1px solid {color}55; "
                f"border-top: 3px solid {color}; "
                f"border-radius: {RADIUS_LG}px; "
                f"padding: {SPACE_LG}px;"
            ):
                ui.label(name).style(
                    f"color: {color}; font-size: 11px; font-weight: 800; "
                    "letter-spacing: 2px;"
                )
                ui.label(title).style(
                    f"color: {COLORS['text']}; font-size: {TEXT_MD}px; "
                    "font-weight: 700;"
                )
                ui.label(body).style(
                    f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
                    "line-height: 1.5;"
                )
                ui.label(where).style(
                    f"color: {color}; font-size: 11px; "
                    "font-family: monospace; margin-top: 4px;"
                )


def _section_loop() -> None:
    ui.label("5 · The self-improving loop").style(heading_style(size=TEXT_LG))
    ui.label(
        "Once the cron daemon is running, this cycle turns every Claude "
        "session into training data without you touching anything."
    ).style(subtitle_style())

    with _card():
        steps = [
            ("Claude calls MCP tool",
             "mcp__planckbot-fs__list_directory", COLORS["info"]),
            ("Proxy records triple",
             "DB row with input + output_raw", COLORS["accent"]),
            ("conversation_scanner",
             "reads Claude Code JSONL log", COLORS["warning"]),
            ("autolabel_precise",
             "fills filtered_output from Claude's reply",
             COLORS["secondary"]),
            ("detect_tool_gaps",
             "flags repeated sequences", COLORS["brass"]),
            ("retrain/synthesize",
             "adapter or new tool gets better",
             COLORS["primary"]),
        ]
        with ui.row().classes("w-full items-stretch gap-1 flex-wrap"):
            for i, (title, body, color) in enumerate(steps):
                with ui.column().classes("gap-1 items-center").style(
                    "flex: 1; min-width: 140px;"
                ):
                    with ui.column().classes("items-center gap-1 w-full").style(
                        f"padding: {SPACE_MD}px {SPACE_SM}px; "
                        f"background: {COLORS['surface2']}; "
                        f"border: 1px solid {color}55; "
                        f"border-radius: 10px; "
                        f"border-top: 3px solid {color};"
                    ):
                        ui.label(f"{i+1}").style(
                            f"color: {color}; font-size: 22px; font-weight: 800;"
                        )
                        ui.label(title).style(
                            f"color: {COLORS['text']}; font-size: {TEXT_SM}px; "
                            "font-weight: 700; text-align: center;"
                        )
                        ui.label(body).style(
                            f"color: {COLORS['text_muted']}; font-size: 11px; "
                            "text-align: center; line-height: 1.3; "
                            "font-family: monospace;"
                        )
                if i < len(steps) - 1:
                    ui.html(
                        f'<div style="display:flex; align-items:center; '
                        f'color: {COLORS["text_muted"]}; font-size: 20px; '
                        f'padding: 0 2px;">→</div>'
                    )

        ui.label(
            "All the cron jobs above are configurable at /cron. "
            "More triples → better auto-labels → more training data → "
            "better adapters → more savings. A loop that improves "
            "while you're doing your actual work."
        ).style(
            f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px; "
            f"margin-top: {SPACE_LG}px; font-style: italic;"
        )


def _section_savings(state) -> None:
    savings = state.triples.token_savings(source="proxy:intervene")

    ui.label("6 · Where the token savings come from").style(
        heading_style(size=TEXT_LG)
    )
    ui.label(
        "Only `proxy:intervene` triples count toward savings — that's the "
        "mode where the tiny LLM actually swaps output for Claude. In "
        "observe mode we watch; we don't save anything."
    ).style(subtitle_style())

    with _card():
        with ui.row().classes("w-full gap-6 items-stretch flex-wrap"):
            with ui.column().classes("gap-2").style(
                "flex: 1; min-width: 260px;"
            ):
                ui.label("What you've seen").style(label_style())
                saved = savings["saved"]
                sign = "+" if saved >= 0 else ""
                color = COLORS["primary"] if saved >= 0 else COLORS["error"]

                with ui.row().classes("items-baseline gap-2"):
                    ui.label(f"{sign}{number_fmt(saved)}").style(
                        f"color: {color}; font-size: 56px; "
                        "font-weight: 800; line-height: 1;"
                    )
                    ui.label("tokens").style(
                        f"color: {COLORS['text_muted']}; "
                        f"font-size: {TEXT_MD}px; font-weight: 600;"
                    )
                pct = (saved / savings["raw_tokens"] * 100) if savings["raw_tokens"] else 0
                ui.label(
                    f"{sign}{pct:.1f}% · across "
                    f"{savings['intervene_count']} interventions"
                ).style(f"color: {color}; font-size: {TEXT_MD}px;")
                ui.label(
                    f"raw total: {number_fmt(savings['raw_tokens'])} "
                    f"— filtered total: {number_fmt(savings['filtered_tokens'])}"
                ).style(
                    f"color: {COLORS['text_muted']}; font-size: {TEXT_SM}px;"
                )

            with ui.column().classes("gap-2").style(
                "flex: 1; min-width: 260px;"
            ):
                ui.label("How to grow this").style(label_style())
                ui.html(
                    f'<ol style="margin: 0; padding-left: 20px; '
                    f'color: {COLORS["text"]}; font-size: {TEXT_SM}px; '
                    f'line-height: 1.8;">'
                    f'<li>Use Claude Code with the planckbot-fs MCP — each '
                    f'call becomes a triple.</li>'
                    f'<li>Let <code style="color: {COLORS["accent"]};">autolabel_precise</code> '
                    f'fill <code style="color: {COLORS["accent"]};">filtered_output</code> from your reply.</li>'
                    f'<li>When enough triples accumulate, retrain the '
                    f'per-tool adapter.</li>'
                    f'<li>Flip proxy mode to <code style="color: {COLORS["primary"]};">intervene</code>. '
                    f'The adapter now swaps outputs live → savings start '
                    f'accumulating here.</li>'
                    f'</ol>'
                )
                if saved < 0:
                    ui.label(
                        "Why is the number negative right now? "
                        "The earliest intervene calls happened with an "
                        "adapter that was under-trained — it expanded "
                        "output instead of compressing it. That's the "
                        "negative we're seeing. Once you retrain with "
                        "more labeled triples, future interventions flip "
                        "the number positive."
                    ).style(
                        f"color: {COLORS['warning']}; font-size: {TEXT_SM}px; "
                        "font-style: italic; line-height: 1.5; "
                        f"padding: {SPACE_MD}px; "
                        f"background: {COLORS['warning']}14; "
                        f"border-radius: 8px; border: 1px solid {COLORS['warning']}44;"
                    )


# --- data helpers -----------------------------------------------------------


def _pick_illustrative_triple(state):
    """Prefer a triple with a real filtered_output (so savings are visible).
    Fall back to the most recent proxy triple. Fall back again to any triple."""
    conn = state.conn
    # 1) Labeled proxy triple with real compression
    row = conn.execute(
        "SELECT * FROM triples "
        "WHERE source LIKE 'proxy:%' AND filtered_output IS NOT NULL "
        "AND output_tokens IS NOT NULL AND filtered_tokens IS NOT NULL "
        "AND filtered_tokens < output_tokens "
        "ORDER BY (output_tokens - filtered_tokens) DESC LIMIT 1"
    ).fetchone()
    # 2) Any proxy triple
    if row is None:
        row = conn.execute(
            "SELECT * FROM triples WHERE source LIKE 'proxy:%' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    # 3) Anything
    if row is None:
        row = conn.execute(
            "SELECT * FROM triples ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        # No triples at all — use a fake so the page still renders
        from planckbot.db.models import Triple
        return Triple(
            tool_name="(no triples yet)",
            input_data='{"hint": "run a prompt to see a real example"}',
            output_data="output will appear here once PlanckBot observes a call",
            input_tokens=8, output_tokens=12,
            source="manual",
        )
    from planckbot.db.models import Triple
    return Triple.from_row(row)


# --- entry point -----------------------------------------------------------


def how_it_works_page():
    state = get_state()

    page_header(
        title="How PlanckBot works",
        subtitle=(
            "End-to-end walkthrough with live data from this install. "
            "Scroll through all six sections — each one shows one piece "
            "of the pipeline."
        ),
    )
    _hero(state)

    ui.space().style(f"height: {SPACE_LG}px;")
    _section_routes()

    ui.space().style(f"height: {SPACE_LG}px;")
    _section_triple(state)

    ui.space().style(f"height: {SPACE_LG}px;")
    _section_proxy_modes()

    ui.space().style(f"height: {SPACE_LG}px;")
    _section_layers()

    ui.space().style(f"height: {SPACE_LG}px;")
    _section_savings(state)

    ui.space().style(f"height: {SPACE_LG}px;")
    _section_loop()
