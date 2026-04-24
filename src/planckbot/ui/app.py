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
        ("Projects", "/projects", "folder_special"),
        ("How it works", "/how-it-works", "school"),
    ]),
    ("Observe", [
        ("Tools", "/tools", "build"),
        ("Activity", "/activity", "bolt"),
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

        # Project switcher — a compact dropdown that changes the DB's
        # active-project flag (and rewrites ~/.claude.json). Shown only
        # when there IS at least one project; otherwise it's a link to
        # the projects page with a "Create one" CTA.
        _sidebar_project_switcher()

        # Unseen-activity badge for /activity. Cheap MAX(id) query.
        activity_unseen = _activity_unseen_count()

        # Groups
        for group_label, items in NAV_GROUPS:
            ui.label(group_label).style(
                f"color: {COLORS['text_dim']}; "
                "font-size: 10px; letter-spacing: 1.5px; "
                "text-transform: uppercase; font-weight: 700; "
                "padding: 12px 8px 4px 8px;"
            )
            for page_label, path, icon in items:
                badge = activity_unseen if path == "/activity" else 0
                _nav_item(
                    page_label, path, icon, current_path, badge=badge,
                )


_STRIP_SOURCE_ICONS: dict[str, str] = {
    "proxy": "📡", "cron": "⏱️", "synth": "🧪",
    "training": "🎓", "ui": "🖥️",
}


def _live_activity_strip(current_path: str) -> None:
    """Sticky 1-line bar at the top of the main column showing the most
    recent activity event, live-updated every 500 ms.

    On pages with `live_seconds` the main body rebuilds periodically;
    this strip lives outside that refresh root so it keeps its own
    timer and latest-event state across rebuilds. The timer polls
    `SELECT * FROM activity_events ORDER BY id DESC LIMIT 1` — trivial.
    """
    from planckbot.ui.state import get_state
    from planckbot import activity as activity_mod

    state = get_state()

    @ui.refreshable
    def _strip():
        events = activity_mod.recent_events(state.conn, limit=1)
        latest = events[-1] if events else None
        with ui.row().classes("w-full items-center no-wrap").style(
            f"background: linear-gradient(90deg, "
            f"{COLORS['surface']} 0%, {COLORS['surface2']} 100%); "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: 10px; "
            f"padding: 6px 12px; margin-bottom: 12px; "
            f"gap: 12px; font-size: 12px; "
            f"box-shadow: 0 0 18px {COLORS['primary']}10;"
        ):
            # pulse dot
            pulse_color = (
                COLORS["primary"] if latest else COLORS["text_dim"]
            )
            ui.label("●").style(
                f"color: {pulse_color}; font-size: 10px; "
                "animation: planck-pulse 1.6s ease-in-out infinite;"
            )
            ui.label("LIVE").style(
                f"color: {COLORS['text_muted']}; "
                "font-weight: 700; letter-spacing: 1.2px; "
                "font-size: 10px;"
            )
            if latest is None:
                ui.label(
                    "waiting for activity — trigger a tool call or "
                    "cron run"
                ).style(
                    f"color: {COLORS['text_muted']}; font-size: 12px; "
                    "flex: 1;"
                )
            else:
                ts_local = latest.ts.astimezone().strftime("%H:%M:%S")
                icon = _STRIP_SOURCE_ICONS.get(latest.source, "•")
                ui.label(ts_local).style(
                    f"color: {COLORS['text_muted']}; "
                    "font-variant-numeric: tabular-nums; "
                    f"flex: 0 0 70px;"
                )
                ui.label(f"{icon} {latest.source}/{latest.kind}").style(
                    f"color: {COLORS['primary']}; font-weight: 600; "
                    "flex: 0 0 170px;"
                )
                ui.label(latest.message).style(
                    f"color: {COLORS['text']}; "
                    "overflow: hidden; text-overflow: ellipsis; "
                    "white-space: nowrap; flex: 1; min-width: 0;"
                )
            ui.link("Full feed →", "/activity").style(
                f"color: {COLORS['accent']}; text-decoration: none; "
                "font-size: 11px; flex: 0 0 auto;"
            )

    _strip()
    ui.timer(0.5, _strip.refresh)


# Events that warrant a cross-page toast. The rest (rx, upstream,
# redact, job_start, job_end) still show up on /activity but are too
# frequent to surface as popups.
_TOAST_KINDS: dict[str, str] = {
    "store":     "positive",
    "intervene": "positive",
    "block":     "warning",
    "activate":  "info",
    "bless":     "positive",
    "job_error": "negative",
    "unbless":   "warning",
}


def _setup_realtime_notifications(current_path: str) -> None:
    """Global cross-page realtime surface.

    500 ms poll on `activity_events`: for each new row whose `kind` is
    in `_TOAST_KINDS`, fire a `ui.notify()`. Skipped on /activity —
    that page is the feed, so duplicating every event as a toast is
    just noise. The sidebar badge also updates on the same tick so it
    feels live on idle pages.
    """
    # The /activity page owns the feed + its own badge-mark logic.
    # Elsewhere, toasts are the reactive signal.
    if current_path == "/activity":
        return
    from planckbot.ui.state import get_state
    from planckbot import activity as activity_mod

    state = get_state()
    # Seed at the current MAX so we don't toast the entire backlog
    # the first time a user lands on a page.
    row = state.conn.execute(
        "SELECT MAX(id) FROM activity_events"
    ).fetchone()
    last = {"id": int(row[0]) if row and row[0] else 0}

    def _tick():
        try:
            new_events = activity_mod.list_events(
                state.conn, since_id=last["id"], limit=20,
            )
        except Exception:
            return
        if not new_events:
            return
        for ev in new_events:
            ntype = _TOAST_KINDS.get(ev.kind)
            if ntype is None:
                continue
            # Short, scannable label. Message is already compact from
            # the emitters; just prefix with the source icon so the
            # toast reads independently of context.
            icon = {
                "proxy": "📡", "cron": "⏱️", "synth": "🧪",
                "training": "🎓", "ui": "🖥️",
            }.get(ev.source, "•")
            ui.notify(
                f"{icon} {ev.message}",
                type=ntype,
                position="top-right",
                timeout=3500,
            )
        last["id"] = new_events[-1].id

    ui.timer(0.5, _tick)


def _activity_unseen_count() -> int:
    """Number of activity_events rows newer than what the user saw last
    time they visited /activity. Returns 0 if the table is missing or
    the query fails — the badge should never take down the sidebar."""
    from planckbot.ui.state import get_state
    state = get_state()
    try:
        row = state.conn.execute(
            "SELECT MAX(id) FROM activity_events WHERE id > ?",
            (state.last_seen_activity_id,),
        ).fetchone()
        if not row or row[0] is None:
            return 0
        count_row = state.conn.execute(
            "SELECT COUNT(*) FROM activity_events WHERE id > ?",
            (state.last_seen_activity_id,),
        ).fetchone()
        return int(count_row[0]) if count_row else 0
    except Exception:
        return 0


def _sidebar_status() -> None:
    """Compact MCP-connection chip beneath the brand: dot + short summary.
    Color changes with health. Clicking goes to /dashboard where the full
    Connection card lives."""
    from planckbot.ui.mcp_status import read_mcp_status
    st = read_mcp_status()

    if st.config_error or not st.configured:
        dot = COLORS["error"]
        line1 = "not connected"
        line2 = "run `planckbot init`"
    elif not st.fs_running:
        dot = COLORS["warning"]
        line1 = "configured · idle"
        line2 = "restart Claude Code"
    else:
        dot = COLORS["success"]
        line1 = f"live · {st.mode or 'observe'}"
        line2 = st._short_path() or "(no path)"

    with ui.link(target="/").classes("no-underline planck-nav-item"):
        with ui.row().classes("items-center gap-2 no-wrap").style(
            f"padding: 6px 10px; margin: 4px 4px 0 4px; "
            f"background: {COLORS['surface2']}; "
            f"border: 1px solid {COLORS['border']}; "
            f"border-radius: 8px; cursor: pointer;"
        ):
            ui.element("div").style(
                f"width: 8px; height: 8px; border-radius: 50%; "
                f"background: {dot}; "
                f"box-shadow: 0 0 8px {dot}; "
                f"animation: planck-pulse 2s ease-in-out infinite;"
                f"flex: 0 0 8px;"
            )
            with ui.column().classes("gap-0").style("min-width: 0;"):
                ui.label(line1).style(
                    f"color: {COLORS['text']}; font-size: 11px; "
                    "font-weight: 600; line-height: 1.2;"
                )
                ui.label(line2).style(
                    f"color: {COLORS['text_muted']}; font-size: 10px; "
                    "font-family: monospace; line-height: 1.2; "
                    "overflow: hidden; text-overflow: ellipsis; "
                    "white-space: nowrap; max-width: 180px;"
                )


def _sidebar_project_switcher() -> None:
    """Compact per-project lens selector beneath the brand.

    Shows the active project's name and a dropdown to switch. Switching
    goes through `ProjectStore.set_active` (DB) and `_rewrite_mcp_upstream_path`
    (~/.claude.json), same as the CLI `planckbot project switch`. User must
    restart Claude Code for the new path to take effect on that side.
    """
    from planckbot.ui.state import get_state
    from planckbot.cli import _rewrite_mcp_upstream_path

    state = get_state()
    projects = state.projects.list_all()
    active = state.active_project()

    if not projects:
        with ui.link(target="/projects").classes(
            "no-underline planck-nav-item"
        ):
            with ui.row().classes("items-center gap-2 no-wrap").style(
                f"padding: 6px 10px; margin: 6px 4px 0 4px; "
                f"background: {COLORS['surface2']}; "
                f"border: 1px dashed {COLORS['border']}; "
                f"border-radius: 8px; cursor: pointer;"
            ):
                ui.icon("folder_special").style(
                    f"color: {COLORS['text_muted']}; font-size: 16px;"
                )
                ui.label("Create a project").style(
                    f"color: {COLORS['text_muted']}; font-size: 11px; "
                    "font-weight: 600;"
                )
        return

    # Build the option map. Include a synthetic "All projects" row so a
    # user can peek at cross-project data without flipping is_active.
    opts: dict[str, str] = {"__all__": "· All projects ·"}
    for p in projects:
        tag = " ★" if p.is_active else ""
        opts[p.id] = f"{p.name}{tag}"

    current_value = (
        state._lens_override if state._lens_override
        else (active.id if active else "__all__")
    )
    if current_value not in opts:
        current_value = "__all__"

    def _on_change(e):
        new_id = e.value
        if new_id == "__all__":
            state.set_lens("__all__")
            ui.notify("lens: all projects", type="info")
        else:
            # Actually switch the DB's active project + ~/.claude.json.
            # This matches the CLI behavior so there's one source of
            # truth per machine.
            project = state.projects.set_active(new_id)
            state.set_lens(None)  # follow the DB again
            ok, msg = _rewrite_mcp_upstream_path(project.path)
            ui.notify(
                f"switched → {project.name}: {msg}",
                type="positive" if ok else "warning",
            )
        ui.navigate.reload()

    with ui.row().classes("items-center no-wrap").style(
        f"padding: 4px 4px 0 4px; margin: 6px 0 0 0;"
    ):
        sel = ui.select(
            opts,
            value=current_value,
            on_change=_on_change,
        ).props("dense outlined options-dense").style(
            "width: 100%; font-size: 11px;"
        )
        sel.classes("planck-project-switcher")


def _nav_item(
    label: str, path: str, icon: str, current_path: str,
    *, badge: int = 0,
) -> None:
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
        with ui.row().classes(
            "items-center gap-2 no-wrap w-full"
        ).style(
            f"padding: 8px 10px; border-radius: 8px; "
            f"background: {bg}; "
            f"border-left: {border}; "
            f"margin: 1px 0;"
        ):
            ui.icon(icon).style(f"color: {icon_color}; font-size: 17px;")
            ui.label(label).style(
                f"color: {text}; font-size: 13px; "
                f"font-weight: {'600' if active else '500'}; "
                "flex: 1;"
            )
            if badge > 0:
                display = str(badge) if badge < 100 else "99+"
                ui.label(display).style(
                    f"background: {COLORS['primary']}; "
                    f"color: {COLORS['bg']}; "
                    "font-size: 10px; font-weight: 700; "
                    "padding: 1px 6px; border-radius: 8px; "
                    "min-width: 16px; text-align: center; "
                    "font-variant-numeric: tabular-nums; "
                    f"box-shadow: 0 0 8px {COLORS['primary']}88;"
                )


def _footer() -> None:
    """Compact branding footer with version + repo links."""
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
        # Sticky live-activity strip above any page content. Rendered
        # OUTSIDE the refreshable wrapper so its own 500 ms timer
        # doesn't get recreated every time the dashboard's 3 s refresh
        # rebuilds the main body.
        if current_path != "/activity":
            _live_activity_strip(current_path)

        if live_seconds is None:
            build_fn()
        else:
            @ui.refreshable
            def _live():
                build_fn()

            _live()
            ui.timer(live_seconds, _live.refresh)
        _footer()

    # Toasts also run on every page. Suppressed on /activity because
    # the feed itself is already the surface.
    _setup_realtime_notifications(current_path)


# --- routes ----------------------------------------------------------------


@ui.page("/")
def index():
    from planckbot.ui.pages.dashboard import dashboard_page
    _page_wrapper(dashboard_page, current_path="/", live_seconds=3.0)


@ui.page("/projects")
def projects():
    from planckbot.ui.pages.projects import projects_page
    # Not live-refreshed: the page has form state (create inputs) that
    # would be wiped by a periodic rebuild. Users reload manually on
    # create/switch/delete.
    _page_wrapper(projects_page, current_path="/projects")


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


@ui.page("/activity")
def activity_route():
    from planckbot.ui.pages.activity import activity_page
    # Not wrapped in `live_seconds` because the page has its own
    # surgical ui.timer that only touches the log column — the filter
    # controls (source + project dropdowns + pause button) must keep
    # their state across ticks.
    _page_wrapper(activity_page, current_path="/activity")


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

        /* Responsive: collapse sidebar on narrow screens. Desktop first,
           then progressively adapt down to mobile. */
        @media (max-width: 900px) {{
            .planck-sidebar {{
                position: static !important;
                width: 100% !important;
                height: auto !important;
                border-right: none !important;
                border-bottom: 1px solid {c['border']} !important;
                flex-direction: column !important;
            }}
            .planck-sidebar-brand {{ padding-bottom: 12px !important; }}
            .q-page > div[style*="margin-left: 240px"] {{
                margin-left: 0 !important;
                padding-left: 20px !important;
                padding-right: 20px !important;
            }}
            /* Stack the in-sidebar nav items into a grid on wide phones */
            .planck-nav-item {{ flex: 1 1 160px; max-width: 220px; }}
        }}
        @media (max-width: 600px) {{
            /* Tighten padding and headings on small phones. */
            .q-page > div[style*="margin-left: 240px"] {{
                padding: 16px 14px 0 14px !important;
            }}
            h1, h2, h3 {{ letter-spacing: -0.2px !important; }}
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

    # Serve docs/ so in-app pages can link to user-facing docs.
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
