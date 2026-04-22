"""NiceGUI app with shared header/nav layout and page routing."""

from nicegui import ui
from planckbot.ui.theme import COLORS, HEADER_STYLE, PAGE_STYLE, NAV_LINK_STYLE
from planckbot.ui.state import get_state


def _header():
    """Shared header with navigation."""
    with ui.header().style(HEADER_STYLE + " height: 56px;"):
        with ui.row().classes("w-full items-center justify-between"):
            # Logo / title
            with ui.row().classes("items-center gap-2"):
                ui.label("PlanckBot").style(
                    f"color: {COLORS['text']}; font-size: 18px; font-weight: 800; "
                    "letter-spacing: -0.5px;"
                )
                ui.label("Research Workbench").style(
                    f"color: {COLORS['text_muted']}; font-size: 13px;"
                )

            # Nav links
            with ui.row().classes("gap-1"):
                for label, path in [
                    ("Dashboard", "/"),
                    ("Experiments", "/experiments"),
                    ("Tools", "/tools"),
                    ("Training", "/training"),
                    ("Models", "/models"),
                    ("Paper Log", "/paper-log"),
                ]:
                    ui.link(label, path).style(NAV_LINK_STYLE).classes("no-underline")


def _page_wrapper(build_fn):
    """Wrap a page builder in the standard layout."""
    ui.colors(primary=COLORS["primary"])
    _header()
    with ui.column().classes("w-full max-w-7xl mx-auto p-6 gap-4").style(
        f"color: {COLORS['text']};"
    ):
        build_fn()


@ui.page("/")
def index():
    from planckbot.ui.pages.dashboard import dashboard_page
    _page_wrapper(dashboard_page)


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


@ui.page("/paper-log")
def paper_log():
    from planckbot.ui.pages.paper_log import paper_log_page
    _page_wrapper(paper_log_page)


def start_app():
    """Launch the NiceGUI application."""
    state = get_state()

    ui.add_head_html(f"""
    <style>
        body {{ background-color: {COLORS['bg']}; }}
        .q-table {{ background-color: {COLORS['surface']} !important; }}
        .q-table thead th {{ color: {COLORS['text_muted']} !important; font-size: 12px !important; }}
        .q-table tbody td {{ color: {COLORS['text']} !important; border-color: {COLORS['border']} !important; }}
        .q-field__label {{ color: {COLORS['text_muted']} !important; }}
        .q-field__native, .q-field__input {{ color: {COLORS['text']} !important; }}
        .q-select .q-field__native {{ color: {COLORS['text']} !important; }}
        .q-card {{ background-color: {COLORS['surface']} !important; }}
        .q-separator {{ background: {COLORS['border']} !important; }}
        .no-underline {{ text-decoration: none !important; }}
        .no-underline:hover {{ background: {COLORS['surface2']} !important; border-radius: 8px; }}
        a {{ color: {COLORS['primary']} !important; text-decoration: none !important; }}
        .q-checkbox__label {{ color: {COLORS['text']} !important; }}
    </style>
    """, shared=True)

    ui.run(
        title="PlanckBot Workbench",
        host=state.config.host,
        port=state.config.port,
        dark=True,
        reload=False,
    )
