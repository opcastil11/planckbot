"""NiceGUI app with shared header/nav layout and page routing."""

from pathlib import Path

from nicegui import app, ui

from planckbot.ui.state import get_state
from planckbot.ui.theme import (
    COLORS,
    HEADER_STYLE,
    NAV_LINK_STYLE,
    PAGE_STYLE,
    WORDMARK_HTML,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
BRANDING_DIR = REPO_ROOT / "static" / "branding"


def _header():
    """Shared header with navigation + brand mark."""
    with ui.header().style(HEADER_STYLE + " height: 64px;"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            # Brand block
            with ui.row().classes("items-center gap-3 no-wrap"):
                ui.image("/branding/planckbots-mark-192.png").style(
                    "width: 36px; height: 36px; border-radius: 50%; "
                    f"box-shadow: 0 0 18px {COLORS['primary']}33, "
                    f"0 0 2px {COLORS['accent']}88; "
                    f"border: 1px solid {COLORS['border_glow']}; "
                    "flex: 0 0 36px; object-fit: cover;"
                )
                with ui.column().classes("gap-0"):
                    ui.html(WORDMARK_HTML).style("font-size: 19px; line-height: 1;")
                    ui.label("Research Workbench").style(
                        f"color: {COLORS['text_muted']}; font-size: 11px; "
                        "letter-spacing: 1.8px; text-transform: uppercase; "
                        "line-height: 1.1; margin-top: 3px;"
                    )

            # Nav links
            with ui.row().classes("gap-1 items-center"):
                for label, path in [
                    ("Dashboard", "/"),
                    ("How it works", "/how-it-works"),
                    ("Experiments", "/experiments"),
                    ("Tools", "/tools"),
                    ("Training", "/training"),
                    ("Models", "/models"),
                    ("Cron", "/cron"),
                    ("Synth", "/synth"),
                    ("Mascots", "/mascots"),
                    ("Paper Log", "/paper-log"),
                ]:
                    ui.link(label, path).style(NAV_LINK_STYLE).classes(
                        "planck-nav-link no-underline"
                    )


def _page_wrapper(build_fn, *, live_seconds: float | None = None):
    """Wrap a page builder in the standard layout.

    live_seconds: if set, the page body is rebuilt on that interval. Only use
    on pages with no form state to preserve (input/textarea values would be
    wiped on each refresh).
    """
    ui.colors(
        primary=COLORS["primary"],
        secondary=COLORS["accent"],
        accent=COLORS["brass"],
        positive=COLORS["success"],
        negative=COLORS["error"],
        info=COLORS["info"],
        warning=COLORS["warning"],
    )
    _header()
    with ui.column().classes("w-full max-w-7xl mx-auto p-6 gap-4").style(
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


@ui.page("/")
def index():
    from planckbot.ui.pages.dashboard import dashboard_page
    _page_wrapper(dashboard_page, live_seconds=3.0)


@ui.page("/experiments")
def experiments():
    from planckbot.ui.pages.experiments import experiments_page
    _page_wrapper(experiments_page)


@ui.page("/experiments/{exp_id}")
def experiment_detail(exp_id: str):
    from planckbot.ui.pages.experiments import _detail_view
    _page_wrapper(lambda: _detail_view(exp_id))


@ui.page("/tools")
def tools():
    from planckbot.ui.pages.tools import tools_page
    _page_wrapper(tools_page)


@ui.page("/training")
def training():
    from planckbot.ui.pages.training import training_page
    _page_wrapper(training_page)


@ui.page("/models")
def models():
    from planckbot.ui.pages.models import models_page
    _page_wrapper(models_page)


@ui.page("/mascots")
def mascots():
    from planckbot.ui.pages.mascots import mascots_page
    _page_wrapper(mascots_page)


@ui.page("/cron")
def cron():
    from planckbot.ui.pages.cron import cron_page
    _page_wrapper(cron_page)


@ui.page("/synth")
def synth():
    from planckbot.ui.pages.synth import synth_page
    _page_wrapper(synth_page, live_seconds=3.0)


@ui.page("/how-it-works")
def how_it_works():
    from planckbot.ui.pages.how_it_works import how_it_works_page
    _page_wrapper(how_it_works_page)


@ui.page("/paper-log")
def paper_log():
    from planckbot.ui.pages.paper_log import paper_log_page
    _page_wrapper(paper_log_page)


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
            box-shadow: 0 1px 0 {c['accent']}12 inset,
                        0 4px 18px rgba(0, 0, 0, 0.4) !important;
            border-radius: 14px !important;
        }}

        .q-table {{ background: {c['surface']} !important; }}
        .q-table thead th {{
            color: {c['text_muted']} !important;
            font-size: 11px !important;
            letter-spacing: 0.5px; text-transform: uppercase;
            border-bottom: 1px solid {c['border']} !important;
        }}
        .q-table tbody td {{
            color: {c['text']} !important;
            border-color: {c['border']} !important;
        }}
        .q-table tbody tr:hover {{ background: {c['surface2']} !important; }}

        .q-field__label,
        .q-field__prefix, .q-field__suffix {{ color: {c['text_muted']} !important; }}
        .q-field__native, .q-field__input,
        .q-field__input::placeholder,
        .q-select .q-field__native {{ color: {c['text']} !important; }}
        .q-field--filled .q-field__control {{
            background: {c['surface2']} !important;
            border-radius: 8px !important;
        }}
        .q-field--outlined .q-field__control:before {{
            border-color: {c['border']} !important;
        }}

        .q-separator {{ background: {c['border']} !important; }}

        /* Nav */
        .no-underline {{ text-decoration: none !important; }}
        .planck-nav-link:hover {{
            color: {c['primary']} !important;
            background: {c['surface2']};
            border-radius: 8px;
        }}

        /* Stat card lift */
        .planck-stat-card:hover {{
            transform: translateY(-2px);
            border-color: {c['border_glow']} !important;
            box-shadow: 0 8px 28px rgba(95, 212, 163, 0.1),
                        0 1px 0 {c['accent']}22 inset !important;
        }}

        /* Mascot gallery card lift (was inline on the page) */
        .planck-mascot-card:hover {{
            transform: translateY(-3px);
            border-color: {c['border_glow']} !important;
            box-shadow: 0 8px 28px rgba(95, 212, 163, 0.12) !important;
        }}

        /* Data pills / tag chips */
        .planck-pill {{
            display: inline-flex; align-items: center; gap: 6px;
            padding: 3px 10px; border-radius: 999px;
            background: {c['surface2']}; color: {c['text']};
            font-size: 11px; font-weight: 600; letter-spacing: 0.3px;
            border: 1px solid {c['border']};
        }}
        .planck-pill--muted {{ color: {c['text_muted']}; }}
        .planck-pill--accent {{
            color: {c['primary']};
            background: {c['primary']}14;
            border-color: {c['primary']}44;
        }}
        .planck-pill--brass {{
            color: {c['brass']};
            background: {c['brass']}14;
            border-color: {c['brass']}44;
        }}

        /* Section label (small-caps) */
        .planck-section-label {{
            color: {c['text_muted']}; font-size: 11px;
            font-weight: 700; letter-spacing: 1px;
            text-transform: uppercase;
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

        /* Selection */
        ::selection {{
            background: {c['primary']}44;
            color: {c['text']};
        }}
    </style>
    """


def start_app():
    """Launch the NiceGUI application."""
    state = get_state()

    # Serve the static/branding folder at /branding/*
    if BRANDING_DIR.exists():
        app.add_static_files("/branding", str(BRANDING_DIR))

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
