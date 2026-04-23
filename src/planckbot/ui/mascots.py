"""Procedural PlanckBots mascot generator.

Every tool that PlanckBot trains on gets its own deterministic mascot —
same tool name always produces the same cute little bot. The mascot's
body hue, halo color, eye style, and tool accessory are all derived from
a hash of the tool name.

Usage:
    from planckbot.ui.mascots import mascot_svg

    svg_string = mascot_svg("file_search", size=96)
    ui.html(svg_string)

The output is a standalone SVG string safe to drop into any HTML context.
No external dependencies — everything is computed from the tool name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


# ---- Tool → accessory mapping -------------------------------------------

# Ordered list so unambiguous matches win (e.g. "grep" before the generic
# "search" keyword). Accessory id must exist in ACCESSORIES below.
_ACCESSORY_KEYWORDS: list[tuple[str, str]] = [
    ("grep",       "magnifier"),
    ("search",     "magnifier"),
    ("find",       "magnifier"),
    ("glob",       "net"),
    ("list",       "scroll"),
    ("read",       "scroll"),
    ("file",       "scroll"),
    ("cat",        "scroll"),
    ("write",      "quill"),
    ("edit",       "quill"),
    ("patch",      "quill"),
    ("bash",       "wrench"),
    ("shell",      "wrench"),
    ("exec",       "wrench"),
    ("run",        "wrench"),
    ("sql",        "flask"),
    ("query",      "flask"),
    ("db",         "flask"),
    ("web",        "antenna"),
    ("fetch",      "antenna"),
    ("http",       "antenna"),
    ("api",        "antenna"),
    ("git",        "bucket"),
    ("commit",     "bucket"),
    ("test",       "flask"),
    ("notebook",   "scroll"),
    ("todo",       "scroll"),
    ("task",       "scroll"),
    ("deploy",     "rocket"),
    ("build",      "wrench"),
    ("compile",    "wrench"),
    ("dummy",      "star"),
    ("echo",       "star"),
]

_DEFAULT_ACCESSORY = "gear"


def _hash_ints(name: str, n: int = 8) -> list[int]:
    """Return n small ints derived from md5(name). Deterministic."""
    h = hashlib.md5(name.encode("utf-8")).digest()
    return [h[i % len(h)] for i in range(n)]


def _pick_accessory(tool_name: str) -> str:
    lower = tool_name.lower()
    for kw, icon in _ACCESSORY_KEYWORDS:
        if kw in lower:
            return icon
    return _DEFAULT_ACCESSORY


@dataclass
class MascotStyle:
    body_hue: int       # 0-359
    halo_hue: int       # 0-359
    orb_hue: int        # 0-359
    eye_dx: int         # -2..2 pupil offset (gives character)
    smile_curve: float  # 3..9, arc height
    accessory: str
    seed: str


def style_for(tool_name: str) -> MascotStyle:
    """Derive a deterministic style from the tool name."""
    h = _hash_ints(tool_name, 8)

    # Body hue: bias towards green/cyan/teal range (120-200) but allow some
    # pinks/purples for variety. We use 60% green-biased, 40% any hue.
    if h[0] < 154:          # 60% of 0-255 → green/teal band
        body_hue = 120 + (h[0] % 80)      # 120..199
    else:
        body_hue = (h[0] * 360 // 256)    # anywhere

    halo_hue = (h[1] * 60 // 256) + 35        # warm gold 35..95
    orb_hue = (body_hue + 60 + (h[2] % 30)) % 360  # complement-ish
    eye_dx = (h[3] % 5) - 2                   # -2..2
    smile_curve = 3 + (h[4] % 7)              # 3..9

    return MascotStyle(
        body_hue=body_hue,
        halo_hue=halo_hue,
        orb_hue=orb_hue,
        eye_dx=eye_dx,
        smile_curve=smile_curve,
        accessory=_pick_accessory(tool_name),
        seed=tool_name,
    )


# ---- Accessory SVG fragments --------------------------------------------

# Each accessory is a small icon drawn around (80, 70) — the bot's right
# side — scaled to roughly 20x20 viewport units.
ACCESSORIES: dict[str, str] = {
    "magnifier": """
        <g stroke="#D4A84A" stroke-width="2" fill="none" transform="translate(72,58)">
          <circle cx="6" cy="6" r="6" fill="#7EE5D6" fill-opacity="0.25"/>
          <line x1="11" y1="11" x2="17" y2="17"/>
        </g>""",
    "scroll": """
        <g transform="translate(70,58)">
          <rect x="0" y="2" width="14" height="14" rx="2"
                fill="#E8F5F0" stroke="#D4A84A" stroke-width="1.5"/>
          <line x1="3" y1="6" x2="11" y2="6" stroke="#6E8BA0" stroke-width="1"/>
          <line x1="3" y1="9" x2="11" y2="9" stroke="#6E8BA0" stroke-width="1"/>
          <line x1="3" y1="12" x2="9" y2="12" stroke="#6E8BA0" stroke-width="1"/>
        </g>""",
    "wrench": """
        <g transform="translate(72,58) rotate(30 7 8)">
          <path d="M2,2 L6,6 L4,8 L0,4 Z M6,6 L14,14 M14,14 L16,12 M14,14 L12,16"
                stroke="#D4A84A" stroke-width="2" fill="none" stroke-linecap="round"
                stroke-linejoin="round"/>
          <circle cx="2" cy="2" r="2" fill="#D4A84A"/>
        </g>""",
    "quill": """
        <g transform="translate(72,56)">
          <path d="M0,14 L10,4 L14,8 L4,18 Z" fill="#7EE5D6" stroke="#D4A84A"
                stroke-width="1.2"/>
          <line x1="0" y1="14" x2="-3" y2="18" stroke="#D4A84A" stroke-width="1.5"/>
        </g>""",
    "net": """
        <g transform="translate(70,58)" fill="none" stroke="#D4A84A" stroke-width="1.2">
          <path d="M0,0 L14,0 L12,12 L2,12 Z" fill="#7EE5D6" fill-opacity="0.25"/>
          <line x1="0" y1="4" x2="14" y2="4"/>
          <line x1="0" y1="8" x2="14" y2="8"/>
          <line x1="4" y1="0" x2="4" y2="12"/>
          <line x1="9" y1="0" x2="10" y2="12"/>
        </g>""",
    "flask": """
        <g transform="translate(72,58)">
          <path d="M4,0 L4,5 L0,14 L12,14 L8,5 L8,0 Z"
                fill="#7EE5D6" fill-opacity="0.5" stroke="#D4A84A" stroke-width="1.2"/>
          <line x1="3" y1="0" x2="9" y2="0" stroke="#D4A84A" stroke-width="1.5"/>
        </g>""",
    "bucket": """
        <g transform="translate(70,58)">
          <path d="M2,2 L14,2 L12,14 L4,14 Z"
                fill="#D4A84A" fill-opacity="0.4" stroke="#D4A84A" stroke-width="1.5"/>
          <path d="M4,2 Q8,-3 12,2" fill="none" stroke="#D4A84A" stroke-width="1.2"/>
        </g>""",
    "antenna": """
        <g transform="translate(72,56)" fill="none" stroke="#D4A84A" stroke-width="1.5">
          <line x1="7" y1="3" x2="7" y2="16"/>
          <path d="M2,8 Q7,2 12,8"/>
          <path d="M4,6 Q7,3 10,6"/>
          <circle cx="7" cy="3" r="1.5" fill="#7EE5D6"/>
        </g>""",
    "rocket": """
        <g transform="translate(72,56)">
          <path d="M7,0 L10,6 L10,14 L4,14 L4,6 Z"
                fill="#E8F5F0" stroke="#D4A84A" stroke-width="1.2"/>
          <circle cx="7" cy="7" r="1.5" fill="#7EE5D6"/>
          <path d="M4,14 L2,18 L7,16 L12,18 L10,14"
                fill="#D4A84A" opacity="0.7"/>
        </g>""",
    "star": """
        <g transform="translate(72,58)">
          <path d="M7,0 L9,5 L14,5 L10,8 L11,13 L7,10 L3,13 L4,8 L0,5 L5,5 Z"
                fill="#D4A84A"/>
        </g>""",
    "gear": """
        <g transform="translate(72,58)" fill="#D4A84A">
          <circle cx="7" cy="7" r="5" fill="none" stroke="#D4A84A" stroke-width="1.8"/>
          <circle cx="7" cy="7" r="1.5"/>
          <rect x="6" y="0" width="2" height="3"/>
          <rect x="6" y="11" width="2" height="3"/>
          <rect x="0" y="6" width="3" height="2"/>
          <rect x="11" y="6" width="3" height="2"/>
        </g>""",
}


# ---- Main renderer -------------------------------------------------------

def mascot_svg(tool_name: str, size: int = 96, title: bool = False) -> str:
    """Render a mascot SVG for the given tool name.

    Deterministic: the same `tool_name` always produces the same mascot.
    Output is a complete standalone <svg> string.
    """
    s = style_for(tool_name)
    body_bright = f"hsl({s.body_hue}, 75%, 70%)"
    body_dark = f"hsl({s.body_hue}, 55%, 40%)"
    body_glow = f"hsl({s.body_hue}, 85%, 80%)"
    halo_color = f"hsl({s.halo_hue}, 75%, 55%)"
    orb_color = f"hsl({s.orb_hue}, 85%, 60%)"
    orb_glow = f"hsl({s.orb_hue}, 90%, 80%)"

    # Unique-ish id so multiple mascots on the page don't clash gradients.
    uid = hashlib.md5(tool_name.encode("utf-8")).hexdigest()[:8]
    accessory = ACCESSORIES.get(s.accessory, ACCESSORIES["gear"])
    eye_r = 4.5
    title_elem = (
        f'<title>PlanckBot for {_escape(tool_name)} — accessory: {s.accessory}</title>'
        if title else ""
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"
        width="{size}" height="{size}"
        role="img" aria-label="PlanckBot mascot for {_escape(tool_name)}">
      {title_elem}
      <defs>
        <radialGradient id="bodyg-{uid}" cx="40%" cy="35%" r="70%">
          <stop offset="0%"  stop-color="{body_glow}"/>
          <stop offset="55%" stop-color="{body_bright}"/>
          <stop offset="100%" stop-color="{body_dark}"/>
        </radialGradient>
        <radialGradient id="orbg-{uid}" cx="40%" cy="40%" r="60%">
          <stop offset="0%"  stop-color="{orb_glow}"/>
          <stop offset="60%" stop-color="{orb_color}"/>
          <stop offset="100%" stop-color="{orb_color}" stop-opacity="0.2"/>
        </radialGradient>
        <filter id="glow-{uid}" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="1.8"/>
        </filter>
      </defs>

      <!-- antennae -->
      <path d="M40,25 Q33,10 28,6" stroke="{body_dark}" stroke-width="2"
            fill="none" stroke-linecap="round"/>
      <path d="M60,25 Q67,10 72,6" stroke="{body_dark}" stroke-width="2"
            fill="none" stroke-linecap="round"/>

      <!-- antenna glow haloes -->
      <circle cx="28" cy="6" r="7" fill="{orb_color}" opacity="0.25"
              filter="url(#glow-{uid})"/>
      <circle cx="72" cy="6" r="7" fill="{orb_color}" opacity="0.25"
              filter="url(#glow-{uid})"/>

      <!-- antenna orbs -->
      <circle cx="28" cy="6" r="4" fill="url(#orbg-{uid})"/>
      <circle cx="72" cy="6" r="4" fill="url(#orbg-{uid})"/>

      <!-- halo -->
      <ellipse cx="50" cy="20" rx="14" ry="2.6" fill="none"
               stroke="{halo_color}" stroke-width="2.2" opacity="0.9"/>
      <ellipse cx="50" cy="20" rx="14" ry="2.6" fill="none"
               stroke="{halo_color}" stroke-width="0.8" opacity="0.6"
               filter="url(#glow-{uid})"/>

      <!-- body -->
      <circle cx="50" cy="52" r="28" fill="url(#bodyg-{uid})"/>

      <!-- body speckles (bioluminescent dots) -->
      <circle cx="38" cy="60" r="1" fill="{body_glow}" opacity="0.8"/>
      <circle cx="62" cy="64" r="1.2" fill="{body_glow}" opacity="0.7"/>
      <circle cx="55" cy="72" r="0.9" fill="{body_glow}" opacity="0.6"/>
      <circle cx="44" cy="70" r="0.7" fill="{body_glow}" opacity="0.6"/>

      <!-- eyes -->
      <circle cx="42" cy="50" r="{eye_r}" fill="#F5FBF8"/>
      <circle cx="58" cy="50" r="{eye_r}" fill="#F5FBF8"/>
      <circle cx="{42 + s.eye_dx}" cy="51" r="2.2" fill="#091520"/>
      <circle cx="{58 + s.eye_dx}" cy="51" r="2.2" fill="#091520"/>
      <circle cx="{43 + s.eye_dx}" cy="49.5" r="0.7" fill="#F5FBF8"/>
      <circle cx="{59 + s.eye_dx}" cy="49.5" r="0.7" fill="#F5FBF8"/>

      <!-- cheeks -->
      <circle cx="36" cy="58" r="2.2" fill="#E06B8B" opacity="0.4"/>
      <circle cx="64" cy="58" r="2.2" fill="#E06B8B" opacity="0.4"/>

      <!-- smile -->
      <path d="M44,62 Q50,{62 + s.smile_curve} 56,62"
            stroke="#091520" stroke-width="1.8" fill="none"
            stroke-linecap="round"/>

      <!-- accessory (deterministic per tool) -->
      {accessory}
    </svg>"""


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
