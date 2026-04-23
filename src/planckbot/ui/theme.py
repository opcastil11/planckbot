"""UI theme — PlanckBots brand palette.

Pulled from the brand mark (bioluminescent plankton + brass gears on deep
ocean-ink background):
  - primary  bioluminescent green body
  - accent   cyan antenna glow
  - brass    halo / gear highlights
  - bg       deep ocean ink
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
