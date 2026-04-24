"""NiceGUI app with sidebar navigation and page routing."""

from pathlib import Path

from nicegui import app, ui

from planckbot.ui.state import get_state
from planckbot.ui.theme import (
    COLORS,
    WORDMARK_HTML,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
BRANDING_DIR = REPO_ROOT / "static" / "branding"

# --- navigation model ------------------------------------------------------

# Semantic groups. Each (group_label, items[]) where items are
# (page_label, path, material-icon).
#
# `Paper log` intentionally omitted from the sidebar — it's an internal
# research-note surface, not end-user facing. The route still exists at
# /paper-log for any existing deep links.
NAV_GROUPS = [
    ("Overview", [
        ("Dashboard", "/", "dashboard"),
        ("How it works", "/how-it-works", "school"),
        ("Paper", "/paper", "description"),
    ]),
    ("Observe", [
        ("Tools", "/tools", "build"),
        ("Experiments", "/experiments", "science"),
    ]),
    ("Train", [
        ("Training", "/training", "model_training"),
        ("Models", "/models", "save"),
    ]),
    ("Automate", [
        ("Cron jobs", "/cron", "schedule"),
        ("Synthesized tools", "/synth", "auto_fix_high"),
    ]),
]

# Routes that remain reachable by URL but are intentionally not in the
# sidebar (internal or infrequently used):
#   /mascots     — procedural mascot gallery
#   /paper-log   — internal research notes


# --- layout primitives -----------------------------------------------------


def _sidebar(current_path: str) -> None:
    """Fixed-width left sidebar with brand, grouped nav, and a status pill.

    Sticky on desktop. On narrow screens (< 900px) the `.planck-sidebar`
    CSS class collapses it into a horizontal strip via a media query in
    `_head_css`.
    """
    with ui.column().classes("planck-sidebar").style(
        f"position: fixed; left: 0; top: 0; "
        f"width: 240px; height: 100vh; "
        f"background: {COLORS['surface']}; "
        f"border-right: 1px solid {COLORS['border']}; "
        f"padding: 20px 10px 16px 14px; "
        f"gap: 4px; overflow-y: auto; z-index: 100;"
    ):
        # Brand
        with ui.row().classes(
            "items-center gap-3 no-wrap planck-sidebar-brand"
        ).style("padding: 0 4px 8px 4px;"):
            ui.image("/branding/planckbots-mark-192.png").style(
                "width: 34px; height: 34px; border-radius: 50%; "
                f"box-shadow: 0 0 18px {COLORS['primary']}33, "
                f"0 0 2px {COLORS['accent']}88; "
                f"border: 1px solid {COLORS['border_glow']}; "
                "flex: 0 0 34px; object-fit: cover;"
            )
            with ui.column().classes("gap-0"):
                ui.html(WORDMARK_HTML).style(
                    "font-size: 16px; line-height: 1; font-weight: 700;"
                )
                ui.label("Workbench").style(
                    f"color: {COLORS['text_muted']}; font-size: 9px; "
                    "letter-spacing: 2px; text-transform: uppercase; "
                    "line-height: 1.1; margin-top: 3px;"
                )

        # Subtle status line (live connection indicator)
        _sidebar_status()

        # Groups
        for group_label, items in NAV_GROUPS:
            ui.label(group_label).style(
                f"color: {COLORS['text_dim']}; "
                "font-size: 10px; letter-spacing: 1.5px; "
                "text-transform: uppercase; font-weight: 700; "
                "padding: 12px 8px 4px 8px;"
            )
            for page_label, path, icon in items:
                _nav_item(page_label, path, icon, current_path)


def _sidebar_status() -> None:
    """Tiny 'system online' chip beneath the brand — reassures on first visit."""
    with ui.row().classes("items-center gap-2 no-wrap").style(
        f"padding: 6px 10px; margin: 4px 4px 0 4px; "
        f"background: {COLORS['surface2']}; "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: 8px;"
    ):
        ui.element("div").style(
            f"width: 8px; height: 8px; border-radius: 50%; "
            f"background: {COLORS['success']}; "
            f"box-shadow: 0 0 8px {COLORS['success']}; "
            f"animation: planck-pulse 2s ease-in-out infinite;"
        )
        ui.label("System online").style(
            f"color: {COLORS['text']}; font-size: 11px; font-weight: 600;"
        )


def _nav_item(label: str, path: str, icon: str, current_path: str) -> None:
    active = (current_path == path) or (
        path != "/" and current_path.startswith(path)
    )
    bg = COLORS["primary"] + "1f" if active else "transparent"
    text = COLORS["text"] if active else COLORS["text_muted"]
    icon_color = COLORS["primary"] if active else COLORS["text_muted"]
    border = (
        f"2px solid {COLORS['primary']}" if active else "2px solid transparent"
    )

    with ui.link(target=path).classes("no-underline planck-nav-item"):
        with ui.row().classes("items-center gap-2 no-wrap").style(
            f"padding: 8px 10px; border-radius: 8px; "
            f"background: {bg}; "
            f"border-left: {border}; "
            f"margin: 1px 0;"
        ):
            ui.icon(icon).style(f"color: {icon_color}; font-size: 17px;")
            ui.label(label).style(
                f"color: {text}; font-size: 13px; "
                f"font-weight: {'600' if active else '500'};"
            )


def _footer() -> None:
    """Compact branding footer with version + repo + paper links."""
    from planckbot import __version__
    with ui.row().classes(
        "w-full items-center justify-between no-wrap flex-wrap"
    ).style(
        f"margin-top: 40px; padding: 16px 0 24px 0; "
        f"border-top: 1px solid {COLORS['border']}; "
        f"color: {COLORS['text_muted']}; font-size: 11px;"
    ):
        ui.label(f"PlanckBot v{__version__} · Apache 2.0").style(
            "letter-spacing: 0.5px;"
        )
        with ui.row().classes("items-center gap-4"):
            for text, url, new_tab in [
                ("How it works", "/how-it-works", False),
                ("Paper", "/paper", False),
                ("GitHub", "https://github.com/opcastil11/planckbot", True),
            ]:
                ui.link(text, url, new_tab=new_tab).classes(
                    "no-underline"
                ).style(f"color: {COLORS['text_muted']}; font-size: 11px;")


def _page_wrapper(
    build_fn,
    *,
    live_seconds: float | None = None,
    current_path: str = "/",
):
    """Wrap a page builder in the standard layout (sidebar + main)."""
    ui.colors(
        primary=COLORS["primary"],
        secondary=COLORS["accent"],
        accent=COLORS["brass"],
        positive=COLORS["success"],
        negative=COLORS["error"],
        info=COLORS["info"],
        warning=COLORS["warning"],
    )
    _sidebar(current_path)

    # Main content shifts right to leave room for the sidebar. Max width
    # caps content line length at ~1200 px for readability.
    with ui.column().classes("w-full").style(
        "margin-left: 240px; "
        "padding: 24px 28px 0 28px; "
        "max-width: 1240px; "
        f"color: {COLORS['text']};"
    ):
        if live_seconds is None:
            build_fn()
        else:
            @ui.refreshable
            def _live():
                build_fn()

            _live()
            ui.timer(live_seconds, _live.refresh)
        _footer()


# --- routes ----------------------------------------------------------------


@ui.page("/")
def index():
    from planckbot.ui.pages.dashboard import dashboard_page
    _page_wrapper(dashboard_page, current_path="/", live_seconds=3.0)


@ui.page("/experiments")
def experiments():
    from planckbot.ui.pages.experiments import experiments_page
    _page_wrapper(experiments_page, current_path="/experiments")


@ui.page("/experiments/{exp_id}")
def experiment_detail(exp_id: str):
    from planckbot.ui.pages.experiments import _detail_view
    _page_wrapper(lambda: _detail_view(exp_id), current_path="/experiments")


@ui.page("/tools")
def tools():
    from planckbot.ui.pages.tools import tools_page
    _page_wrapper(tools_page, current_path="/tools")


@ui.page("/training")
def training():
    from planckbot.ui.pages.training import training_page
    _page_wrapper(training_page, current_path="/training")


@ui.page("/models")
def models():
    from planckbot.ui.pages.models import models_page
    _page_wrapper(models_page, current_path="/models")


@ui.page("/mascots")
def mascots():
    from planckbot.ui.pages.mascots import mascots_page
    _page_wrapper(mascots_page, current_path="/mascots")


@ui.page("/cron")
def cron():
    from planckbot.ui.pages.cron import cron_page
    _page_wrapper(cron_page, current_path="/cron")


@ui.page("/synth")
def synth():
    from planckbot.ui.pages.synth import synth_page
    _page_wrapper(synth_page, current_path="/synth", live_seconds=3.0)


@ui.page("/how-it-works")
def how_it_works():
    from planckbot.ui.pages.how_it_works import how_it_works_page
    _page_wrapper(how_it_works_page, current_path="/how-it-works")


@ui.page("/paper")
def paper():
    from planckbot.ui.pages.paper import paper_page
    _page_wrapper(paper_page, current_path="/paper")


@ui.page("/paper-log")
def paper_log():
    # Kept alive but not shown in the sidebar — internal research notes.
    from planckbot.ui.pages.paper_log import paper_log_page
    _page_wrapper(paper_log_page, current_path="/paper-log")


def _head_css() -> str:
    """Generate <style> block applying the PlanckBots palette to Quasar."""
    c = COLORS
    return f"""
    <link rel="icon" type="image/png" href="/branding/favicon.png">
    <style>
        html, body {{
            background: radial-gradient(1400px 900px at 15% -10%,
                {c['primary']}0d 0%, transparent 55%),
              radial-gradient(1100px 700px at 85% 110%,
                {c['accent']}0a 0%, transparent 60%),
              {c['bg']};
            color: {c['text']};
        }}

        /* Quasar component overrides */
        .q-page {{ background: transparent !important; }}

        .q-card {{
            background: linear-gradient(180deg,
                {c['surface']} 0%, {c['bg']} 140%) !important;
            border: 1px solid {c['border']};
            border-radius: 14px !important;
            box-shadow: 0 1px 0 {c['border_glow']}22 inset,
                0 12px 40px rgba(0, 0, 0, 0.35) !important;
        }}

        /* Headings */
        h1, h2, h3, h4, h5 {{
            color: {c['text']};
            letter-spacing: -0.3px;
            font-weight: 700;
        }}

        /* Sidebar */
        .planck-sidebar-brand:hover {{ cursor: default; }}
        .planck-nav-item:hover > div {{
            background: {c['surface2']} !important;
        }}

        /* Status dot pulse (sidebar) */
        @keyframes planck-pulse {{
            0%, 100% {{ opacity: 1; transform: scale(1); }}
            50%      {{ opacity: 0.5; transform: scale(0.88); }}
        }}

        /* Responsive: collapse sidebar on narrow screens */
        @media (max-width: 900px) {{
            .planck-sidebar {{
                position: static !important;
                width: 100% !important;
                height: auto !important;
                border-right: none !important;
                border-bottom: 1px solid {c['border']} !important;
            }}
            .planck-sidebar-brand {{ padding-bottom: 12px !important; }}
            .q-page > div[style*="margin-left: 240px"] {{
                margin-left: 0 !important;
                padding-left: 20px !important;
                padding-right: 20px !important;
            }}
        }}

        /* Links */
        a {{ color: {c['primary']} !important; text-decoration: none !important; }}
        a:hover {{ color: {c['accent']} !important; }}

        /* Buttons */
        .q-btn--standard.bg-primary {{
            background: linear-gradient(135deg,
                {c['primary']} 0%, {c['primary_dim']} 100%) !important;
            color: {c['bg']} !important;
            box-shadow: 0 0 20px {c['primary']}33 !important;
            font-weight: 600;
        }}

        /* Badges & chips: warm brass for highlights */
        .q-badge, .q-chip {{ border-radius: 6px !important; }}

        /* Checkbox labels readable on dark */
        .q-checkbox__label {{ color: {c['text']} !important; }}

        /* Scrollbar */
        ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
        ::-webkit-scrollbar-track {{ background: {c['bg']}; }}
        ::-webkit-scrollbar-thumb {{
            background: {c['surface2']};
            border-radius: 5px;
        }}
        ::-webkit-scrollbar-thumb:hover {{ background: {c['border_glow']}; }}

        /* Pills (planck-pill class, used by some pages) */
        .planck-pill {{
            display: inline-block; padding: 2px 8px; border-radius: 999px;
            font-size: 11px; font-weight: 600; letter-spacing: 0.3px;
            background: {c['surface2']}; color: {c['text_muted']};
            border: 1px solid {c['border']};
        }}
        .planck-pill--accent {{
            color: {c['accent']}; border-color: {c['accent']}44;
            background: {c['accent']}14;
        }}
        .planck-pill--muted {{
            color: {c['text_muted']}; border-color: {c['border']};
        }}
    </style>
    """


def start_app():
    """Launch the NiceGUI application."""
    state = get_state()

    # Serve the static/branding folder at /branding/*
    if BRANDING_DIR.exists():
        app.add_static_files("/branding", str(BRANDING_DIR))

    # Serve docs/ so the paper page can link to its PDF.
    docs_dir = REPO_ROOT / "docs"
    if docs_dir.exists():
        app.add_static_files("/docs", str(docs_dir))

    ui.add_head_html(_head_css(), shared=True)

    favicon_path = BRANDING_DIR / "favicon.png"

    ui.run(
        title="PlanckBots — Research Workbench",
        favicon=str(favicon_path) if favicon_path.exists() else None,
        host=state.config.host,
        port=state.config.port,
        dark=True,
        reload=False,
    )
