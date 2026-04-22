"""UI theme and styling constants."""

# Color palette
COLORS = {
    "primary": "#6366f1",      # Indigo
    "secondary": "#8b5cf6",    # Violet
    "success": "#22c55e",      # Green
    "warning": "#f59e0b",      # Amber
    "error": "#ef4444",        # Red
    "info": "#3b82f6",         # Blue
    "bg": "#0f172a",           # Slate 900
    "surface": "#1e293b",      # Slate 800
    "surface2": "#334155",     # Slate 700
    "text": "#f8fafc",         # Slate 50
    "text_muted": "#94a3b8",   # Slate 400
    "border": "#475569",       # Slate 600
}

# Status colors
STATUS_COLORS = {
    "planned": COLORS["info"],
    "running": COLORS["warning"],
    "completed": COLORS["success"],
    "failed": COLORS["error"],
    "cancelled": COLORS["text_muted"],
}

# Common styles
CARD_STYLE = (
    f"background-color: {COLORS['surface']}; "
    f"border: 1px solid {COLORS['border']}; "
    "border-radius: 12px; padding: 20px;"
)

PAGE_STYLE = f"background-color: {COLORS['bg']}; min-height: 100vh;"

HEADER_STYLE = (
    f"background-color: {COLORS['surface']}; "
    f"border-bottom: 1px solid {COLORS['border']}; "
    "padding: 0 24px;"
)

NAV_LINK_STYLE = (
    f"color: {COLORS['text_muted']}; text-decoration: none; "
    "padding: 8px 16px; border-radius: 8px; font-size: 14px;"
)

NAV_LINK_ACTIVE = (
    f"color: {COLORS['text']}; text-decoration: none; "
    f"background-color: {COLORS['surface2']}; "
    "padding: 8px 16px; border-radius: 8px; font-size: 14px;"
)
