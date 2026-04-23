"""UI theme — PlanckBots brand palette + typography + motion.

Palette pulled from the brand mark (bioluminescent plankton + brass gears
on deep ocean-ink background):
  - primary  bioluminescent green body
  - accent   cyan antenna glow
  - brass    halo / gear highlights
  - bg       deep ocean ink

Typography and motion constants are defined below for consistent
use across every page. Prefer these over inline style strings.
"""

# Color palette
COLORS = {
    # Brand
    "primary":     "#5FD4A3",   # bioluminescent green (body)
    "primary_dim": "#3FA07D",   # darker green for hover
    "accent":      "#7EE5D6",   # cyan glow (antennae orbs)
    "brass":       "#D4A84A",   # warm brass (halo + gears)
    "brass_dim":   "#A07F32",

    # Semantic (mapped to brand)
    "secondary":   "#9F7EE5",   # soft lavender, complementary
    "success":     "#5FD4A3",
    "warning":     "#D4A84A",
    "error":       "#E06B6B",
    "info":        "#7EE5D6",

    # Surfaces (deep ocean → lifted panels)
    "bg":          "#07121C",   # ocean ink
    "surface":     "#0F1E2D",   # panel
    "surface2":    "#162B3E",   # hover / emphasized panel
    "border":      "#1E3A50",   # subtle edge
    "border_glow": "#2F6B8E",   # active edge (with glow)

    # Typography
    "text":        "#E8F5F0",   # creamy white
    "text_muted":  "#6E8BA0",   # muted blue-gray
    "text_dim":    "#4A6578",
}

# Status colors
STATUS_COLORS = {
    "planned":   COLORS["info"],
    "running":   COLORS["warning"],
    "completed": COLORS["success"],
    "failed":    COLORS["error"],
    "cancelled": COLORS["text_muted"],
}

# Common styles
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

# Wordmark: "Planck" in green, "Bots" in cyan — mirrors the logo's split gradient
WORDMARK_HTML = (
    '<span style="color: {primary}; font-weight: 800; letter-spacing: -0.3px;">Planck</span>'
    '<span style="color: {accent}; font-weight: 800; letter-spacing: -0.3px;">Bots</span>'
).format(primary=COLORS["primary"], accent=COLORS["accent"])


# --- Typography system ---------------------------------------------------
# All font-size values in px. Use with style strings: f"font-size: {TEXT_LG}px"
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


# --- Elevated card (gradient + soft inner highlight + drop shadow) -------
ELEVATED_CARD_STYLE = (
    f"background: linear-gradient(180deg, {COLORS['surface']} 0%, "
    f"{COLORS['bg']} 140%); "
    f"border: 1px solid {COLORS['border']}; "
    f"border-radius: {RADIUS_LG}px; padding: {SPACE_LG}px; "
    f"box-shadow: 0 1px 0 {COLORS['accent']}12 inset, "
    "0 8px 24px rgba(0, 0, 0, 0.35);"
)


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
