"""UI theme — PlanckBots brand palette + typography + motion.

Two palettes are defined: a dark "ocean-ink" mode (default) and a light
"surface" mode. The active palette is read from `data/ui_mode.txt` on
import; switch via `set_mode("dark"|"light")`.

Why it works the way it does:
- `COLORS` is a module-level dict, mutated in-place by `set_mode()`. Pages
  that read `COLORS["bg"]` at render time pick up the change after a page
  reload (NiceGUI `ui.navigate.reload()`).
- Constants like `CARD_STYLE` are recomputed by `set_mode()` and re-bound
  on the module — pages that did `from planckbot.ui.theme import CARD_STYLE`
  before the toggle keep their old binding and need a process restart to
  see the new value. Pages that access via `theme.CARD_STYLE` see the new
  value immediately. Trade-off taken to avoid a 11-file sweep; documented
  above the toggle in `app.py`.
"""

from __future__ import annotations

from pathlib import Path


# --- Palettes --------------------------------------------------------------

_DARK_PALETTE: dict[str, str] = {
    # Brand
    "primary":     "#5FD4A3",
    "primary_dim": "#3FA07D",
    "accent":      "#7EE5D6",
    "brass":       "#D4A84A",
    "brass_dim":   "#A07F32",

    # Semantic
    "secondary":   "#9F7EE5",
    "success":     "#5FD4A3",
    "warning":     "#D4A84A",
    "error":       "#E06B6B",
    "info":        "#7EE5D6",

    # Surfaces
    "bg":          "#07121C",
    "surface":     "#0F1E2D",
    "surface2":    "#162B3E",
    "border":      "#1E3A50",
    "border_glow": "#2F6B8E",

    # Typography
    "text":        "#E8F5F0",
    "text_muted":  "#6E8BA0",
    "text_dim":    "#4A6578",
}

_LIGHT_PALETTE: dict[str, str] = {
    # Brand — slightly desaturated for legibility on white
    "primary":     "#2EA37A",
    "primary_dim": "#1F7E5D",
    "accent":      "#1FA897",
    "brass":       "#A8801A",
    "brass_dim":   "#7E5F11",

    # Semantic
    "secondary":   "#7458C9",
    "success":     "#2EA37A",
    "warning":     "#A8801A",
    "error":       "#C44545",
    "info":        "#1FA897",

    # Surfaces — soft warm white, never pure #FFFFFF (eye fatigue)
    "bg":          "#F5F2EB",   # cream paper
    "surface":     "#FFFFFF",   # cards
    "surface2":    "#EBE7DD",   # hover / emphasized panel
    "border":      "#D8D0C0",
    "border_glow": "#9FB8B0",

    # Typography
    "text":        "#1A2433",
    "text_muted":  "#5A6B7A",
    "text_dim":    "#8F9BA8",
}

# Active palette. Mutated in-place by set_mode().
COLORS: dict[str, str] = dict(_DARK_PALETTE)


# --- Persistence -----------------------------------------------------------

def _persist_path() -> Path:
    """Where the chosen mode is stored. Lives next to the SQLite DB so it
    travels with the install (and is gitignored via data/)."""
    from planckbot.config import config
    return config.data_dir / "ui_mode.txt"


def _read_persisted_mode() -> str:
    p = _persist_path()
    if not p.exists():
        return "dark"
    try:
        v = p.read_text().strip().lower()
    except OSError:
        return "dark"
    return v if v in ("dark", "light") else "dark"


def _write_persisted_mode(mode: str) -> None:
    p = _persist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(mode.strip().lower() + "\n")


def current_mode() -> str:
    """Return the active mode ('dark' or 'light')."""
    # Cheap heuristic: check a sentinel color in COLORS.
    return "dark" if COLORS.get("bg") == _DARK_PALETTE["bg"] else "light"


def set_mode(mode: str, *, persist: bool = True) -> None:
    """Switch palette. Mutates COLORS in-place + recomputes derived
    constants. Persists to disk unless `persist=False` (tests).

    Pages that already imported the top-level constants (CARD_STYLE etc.)
    by name will keep their old bindings — they need a process restart.
    Pages that read COLORS[...] live or theme.CARD_STYLE through the
    module pick up the change after `ui.navigate.reload()`.
    """
    mode = mode.strip().lower()
    if mode not in ("dark", "light"):
        raise ValueError(f"unknown mode: {mode!r}")
    palette = _DARK_PALETTE if mode == "dark" else _LIGHT_PALETTE
    COLORS.clear()
    COLORS.update(palette)
    _recompute_derived()
    if persist:
        _write_persisted_mode(mode)


# --- Status colors (mode-dependent) ---------------------------------------

STATUS_COLORS: dict[str, str] = {}


# --- Common styles (recomputed by set_mode) -------------------------------

CARD_STYLE: str = ""
PAGE_STYLE: str = ""
HEADER_STYLE: str = ""
NAV_LINK_STYLE: str = ""
NAV_LINK_ACTIVE: str = ""
ELEVATED_CARD_STYLE: str = ""
WORDMARK_HTML: str = ""


def _recompute_derived() -> None:
    """Rebuild every constant that bakes hex values from COLORS.

    Called by set_mode() and at import. Mutates module globals so
    `theme.CARD_STYLE` (attribute access) returns fresh values; direct
    `from theme import CARD_STYLE` bindings stay frozen until restart.
    """
    global STATUS_COLORS
    global CARD_STYLE, PAGE_STYLE, HEADER_STYLE
    global NAV_LINK_STYLE, NAV_LINK_ACTIVE
    global ELEVATED_CARD_STYLE, WORDMARK_HTML

    STATUS_COLORS = {
        "planned":   COLORS["info"],
        "running":   COLORS["warning"],
        "completed": COLORS["success"],
        "failed":    COLORS["error"],
        "cancelled": COLORS["text_muted"],
    }

    CARD_STYLE = (
        f"background: linear-gradient(180deg, {COLORS['surface']} 0%, {COLORS['bg']} 120%); "
        f"border: 1px solid {COLORS['border']}; "
        "border-radius: 14px; padding: 20px; "
        "box-shadow: 0 1px 0 rgba(126, 229, 214, 0.04) inset, 0 4px 16px rgba(0,0,0,0.35);"
    )

    PAGE_STYLE = (
        f"background: radial-gradient(1200px 800px at 20% -10%, "
        f"rgba(95, 212, 163, 0.05) 0%, transparent 60%), "
        f"{COLORS['bg']}; min-height: 100vh;"
    )

    HEADER_STYLE = (
        f"background: linear-gradient(180deg, rgba(22, 43, 62, 0.95) 0%, rgba(15, 30, 45, 0.85) 100%); "
        f"backdrop-filter: blur(8px); "
        f"border-bottom: 1px solid {COLORS['border']}; "
        "padding: 0 24px; "
        "box-shadow: 0 1px 0 rgba(126, 229, 214, 0.08) inset;"
    )

    NAV_LINK_STYLE = (
        f"color: {COLORS['text_muted']}; text-decoration: none; "
        "padding: 8px 16px; border-radius: 8px; font-size: 13px; "
        "font-weight: 500; letter-spacing: 0.2px; "
        "transition: all 120ms ease;"
    )

    NAV_LINK_ACTIVE = (
        f"color: {COLORS['primary']}; text-decoration: none; "
        f"background: {COLORS['surface2']}; "
        f"border: 1px solid {COLORS['border']}; "
        "padding: 8px 16px; border-radius: 8px; font-size: 13px; "
        "font-weight: 600; letter-spacing: 0.2px; "
        "box-shadow: 0 0 20px rgba(95, 212, 163, 0.15);"
    )

    ELEVATED_CARD_STYLE = (
        f"background: linear-gradient(180deg, {COLORS['surface']} 0%, "
        f"{COLORS['bg']} 140%); "
        f"border: 1px solid {COLORS['border']}; "
        f"border-radius: 16px; padding: 24px; "
        f"box-shadow: 0 1px 0 {COLORS['accent']}12 inset, "
        "0 8px 24px rgba(0, 0, 0, 0.35);"
    )

    WORDMARK_HTML = (
        '<span style="color: {primary}; font-weight: 800; letter-spacing: -0.3px;">Planck</span>'
        '<span style="color: {accent}; font-weight: 800; letter-spacing: -0.3px;">Bots</span>'
    ).format(primary=COLORS["primary"], accent=COLORS["accent"])


# --- Typography system ----------------------------------------------------
TEXT_XXL = 32
TEXT_XL = 24
TEXT_LG = 18
TEXT_MD = 14
TEXT_SM = 12
TEXT_XS = 11

FONT_STACK = (
    '-apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
)
MONO_STACK = (
    '"JetBrains Mono", "Fira Code", Menlo, Consolas, "Courier New", monospace'
)


def heading_style(size: int = TEXT_XL, color: str | None = None) -> str:
    """Big, confident page/section heading."""
    return (
        f"color: {color or COLORS['text']}; "
        f"font-size: {size}px; font-weight: 700; "
        "letter-spacing: -0.4px; line-height: 1.2;"
    )


def subtitle_style() -> str:
    """Muted supporting line under a heading."""
    return (
        f"color: {COLORS['text_muted']}; "
        f"font-size: {TEXT_MD}px; "
        "letter-spacing: 0.1px; line-height: 1.4;"
    )


def label_style(emphasis: str = "normal") -> str:
    """Small-caps style label for stat titles / form fields."""
    color = COLORS["text_muted"] if emphasis == "normal" else COLORS["text"]
    return (
        f"color: {color}; "
        f"font-size: {TEXT_XS}px; font-weight: 600; "
        "letter-spacing: 0.8px; text-transform: uppercase;"
    )


def body_style(color: str | None = None, muted: bool = False) -> str:
    col = color or (COLORS["text_muted"] if muted else COLORS["text"])
    return f"color: {col}; font-size: {TEXT_MD}px; line-height: 1.55;"


def number_style(color: str | None = None, size: int = TEXT_XXL) -> str:
    """For prominent statistics — the hero of a data UI."""
    return (
        f"color: {color or COLORS['text']}; "
        f"font-size: {size}px; font-weight: 800; "
        f"font-family: {FONT_STACK}; "
        "letter-spacing: -1px; line-height: 1; "
        "font-variant-numeric: tabular-nums;"
    )


def mono_style(color: str | None = None, size: int = TEXT_SM) -> str:
    return (
        f"color: {color or COLORS['text']}; "
        f"font-family: {MONO_STACK}; "
        f"font-size: {size}px; line-height: 1.5;"
    )


# --- Motion --------------------------------------------------------------
EASE_OUT = "cubic-bezier(0.16, 1, 0.3, 1)"
EASE_IN_OUT = "cubic-bezier(0.65, 0, 0.35, 1)"
DURATION_FAST = "120ms"
DURATION_NORMAL = "200ms"
DURATION_SLOW = "340ms"

TRANSITION_DEFAULT = f"all {DURATION_NORMAL} {EASE_OUT}"


# --- Layout --------------------------------------------------------------
RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 16
RADIUS_PILL = 999

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 16
SPACE_LG = 24
SPACE_XL = 32


# --- Helpers: format numbers like "1,234" and relative time --------------
def number_fmt(n: int | float, decimals: int = 0) -> str:
    if isinstance(n, float):
        return f"{n:,.{decimals}f}"
    return f"{n:,}"


def relative_time(iso_timestamp: str | None) -> str:
    """Convert an ISO8601 UTC timestamp into a short relative string.

    Falls back to the raw 16-char prefix if the string can't be parsed.
    """
    if not iso_timestamp:
        return "—"
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - t
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            m = secs // 60
            return f"{m}m ago"
        if secs < 86400:
            h = secs // 3600
            return f"{h}h ago"
        if secs < 7 * 86400:
            d = secs // 86400
            return f"{d}d ago"
        if secs < 30 * 86400:
            w = secs // (7 * 86400)
            return f"{w}w ago"
        return t.date().isoformat()
    except (ValueError, TypeError):
        return iso_timestamp[:16]


# Initialize: read persisted mode and apply.
try:
    set_mode(_read_persisted_mode(), persist=False)
except Exception:
    # Never let theme init block UI startup — fall back to dark.
    set_mode("dark", persist=False)
