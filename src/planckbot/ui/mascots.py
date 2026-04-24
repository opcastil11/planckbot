"""Procedural PlanckBots mascot generator.

Every tool that PlanckBot observes gets its own deterministic mascot —
same tool name always produces the same little bot. Mascots are drawn in
the PlanckBots house style: translucent jellyfish-like body with internal
bubbles, brass halo and side-gears, antennae with glowing orbs, large
expressive eyes, and a wide open grin. A per-tool accessory reflects what
the tool does (magnifier for search, scroll for read, wrench for shell …).

Usage:
    from planckbot.ui.mascots import mascot_svg
    ui.html(mascot_svg("file_search", size=96))
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


# ---- Tool → accessory mapping -------------------------------------------

# Ordered — first match wins. Accessory id must exist in ACCESSORIES below.
_ACCESSORY_KEYWORDS: list[tuple[str, str]] = [
    ("grep",       "magnifier"),
    ("search",     "magnifier"),
    ("find",       "magnifier"),
    ("glob",       "net"),
    ("list",       "scroll"),
    ("read",       "scroll"),
    ("file",       "scroll"),
    ("cat",        "scroll"),
    ("tree",       "scroll"),
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
    ("test",       "flask"),
    ("web",        "antenna"),
    ("fetch",      "antenna"),
    ("http",       "antenna"),
    ("api",        "antenna"),
    ("git",        "bucket"),
    ("commit",     "bucket"),
    ("notebook",   "scroll"),
    ("todo",       "scroll"),
    ("task",       "scroll"),
    ("deploy",     "rocket"),
    ("build",      "wrench"),
    ("compile",    "wrench"),
    ("dummy",      "star"),
    ("echo",       "star"),
    ("directory",  "scroll"),
    ("move",       "bucket"),
    ("create",     "quill"),
]

_DEFAULT_ACCESSORY = "gear"


def _hash_ints(name: str, n: int = 12) -> list[int]:
    """Return n small ints derived from md5(name). Deterministic."""
    h = hashlib.md5(name.encode("utf-8")).digest()
    return [h[i % len(h)] for i in range(n)]


def _pick_accessory(tool_name: str) -> str:
    lower = tool_name.lower()
    for kw, icon in _ACCESSORY_KEYWORDS:
        if kw in lower:
            return icon
    return _DEFAULT_ACCESSORY


# ---- Style ---------------------------------------------------------------


@dataclass
class MascotStyle:
    # The brand body is a pale teal with slight per-tool drift so mascots
    # feel like siblings, not random rainbow characters.
    body_hue: int       # 145..185 (green-teal band)
    orb_hue: int        # cyan 175..200 or warm brass 40..55
    eye_shine_dx: int   # -1..1 pupil micro-offset → subtle expression
    smile_curve: float  # 1..4, slight grin variation
    bubble_seed: int    # 0..255, positions the internal bubbles
    accessory: str
    seed: str


def style_for(tool_name: str) -> MascotStyle:
    """Derive a deterministic style from the tool name."""
    h = _hash_ints(tool_name, 12)

    # Body stays in the teal band (145..185) — all mascots are recognizably
    # from the same family.
    body_hue = 145 + (h[0] % 41)
    # Orb hue: 80% cyan, 20% warm brass. Prevents too many yellow orbs.
    orb_hue = (165 + (h[1] % 35)) if (h[1] % 5) else (40 + (h[1] % 15))

    return MascotStyle(
        body_hue=body_hue,
        orb_hue=orb_hue,
        eye_shine_dx=(h[2] % 3) - 1,
        smile_curve=1 + (h[3] % 4),
        bubble_seed=h[4],
        accessory=_pick_accessory(tool_name),
        seed=tool_name,
    )


# ---- Accessory SVG fragments (small, ~14×14, positioned by the caller) --

# Each accessory is drawn to sit inside or at the lower-right of the body.
# Color hints use the brand palette: #D4A84A (brass), #7EE5D6 (cyan),
# #E8F5F0 (cream). Keep stroke-width ~1–1.5 — we're rendering at 48–96 px.
ACCESSORIES: dict[str, str] = {
    "magnifier": """
        <g stroke="#D4A84A" stroke-width="1.3" fill="none">
          <circle cx="68" cy="72" r="5" fill="#7EE5D6" fill-opacity="0.35"/>
          <line x1="72" y1="76" x2="77" y2="81"/>
        </g>""",
    "scroll": """
        <g transform="translate(61,67)">
          <rect x="0" y="0" width="12" height="11" rx="1.5"
                fill="#E8F5F0" stroke="#D4A84A" stroke-width="1"/>
          <line x1="2" y1="3" x2="10" y2="3" stroke="#6E8BA0" stroke-width="0.8"/>
          <line x1="2" y1="6" x2="10" y2="6" stroke="#6E8BA0" stroke-width="0.8"/>
          <line x1="2" y1="9" x2="8" y2="9" stroke="#6E8BA0" stroke-width="0.8"/>
        </g>""",
    "wrench": """
        <g transform="translate(62,66) rotate(30 6 6)">
          <path d="M0,3 L3,0 L6,3 L3,6 Z M3,6 L11,11 M11,11 L13,9 M11,11 L9,13"
                stroke="#D4A84A" stroke-width="1.5" fill="none"
                stroke-linecap="round" stroke-linejoin="round"/>
        </g>""",
    "quill": """
        <g transform="translate(61,65)">
          <path d="M0,12 L8,4 L11,7 L3,15 Z"
                fill="#7EE5D6" stroke="#D4A84A" stroke-width="1"/>
          <line x1="0" y1="12" x2="-2" y2="15" stroke="#D4A84A"
                stroke-width="1.2"/>
        </g>""",
    "net": """
        <g transform="translate(61,67)" fill="none" stroke="#D4A84A"
           stroke-width="1">
          <path d="M0,0 L12,0 L10,11 L2,11 Z"
                fill="#7EE5D6" fill-opacity="0.28"/>
          <line x1="0" y1="3" x2="12" y2="3"/>
          <line x1="0" y1="7" x2="12" y2="7"/>
          <line x1="3" y1="0" x2="3" y2="11"/>
          <line x1="8" y1="0" x2="9" y2="11"/>
        </g>""",
    "flask": """
        <g transform="translate(62,67)">
          <path d="M3,0 L3,4 L0,12 L10,12 L7,4 L7,0 Z"
                fill="#7EE5D6" fill-opacity="0.55"
                stroke="#D4A84A" stroke-width="1"/>
          <line x1="2" y1="0" x2="8" y2="0" stroke="#D4A84A" stroke-width="1.2"/>
        </g>""",
    "bucket": """
        <g transform="translate(61,67)">
          <path d="M1,1 L11,1 L9,11 L3,11 Z"
                fill="#D4A84A" fill-opacity="0.45"
                stroke="#D4A84A" stroke-width="1.2"/>
          <path d="M3,1 Q6,-2 9,1" fill="none" stroke="#D4A84A" stroke-width="1"/>
        </g>""",
    "antenna": """
        <g transform="translate(62,65)" fill="none" stroke="#D4A84A"
           stroke-width="1.2">
          <line x1="5" y1="3" x2="5" y2="13"/>
          <path d="M1,7 Q5,2 9,7"/>
          <path d="M3,6 Q5,3 7,6"/>
          <circle cx="5" cy="3" r="1.2" fill="#7EE5D6"/>
        </g>""",
    "rocket": """
        <g transform="translate(62,65)">
          <path d="M5,0 L8,5 L8,12 L3,12 L3,5 Z"
                fill="#E8F5F0" stroke="#D4A84A" stroke-width="1"/>
          <circle cx="5.5" cy="6" r="1.1" fill="#7EE5D6"/>
          <path d="M3,12 L1,15 L5.5,13.5 L10,15 L8,12"
                fill="#D4A84A" opacity="0.7"/>
        </g>""",
    "star": """
        <g transform="translate(62,68)">
          <path d="M6,0 L7.5,4.2 L12,4.5 L8.5,7.2 L9.5,11.5 L6,9 L2.5,11.5
                   L3.5,7.2 L0,4.5 L4.5,4.2 Z"
                fill="#D4A84A"/>
        </g>""",
    "gear": """
        <g transform="translate(62,67)">
          <circle cx="6" cy="6" r="4" fill="none"
                  stroke="#D4A84A" stroke-width="1.3"/>
          <circle cx="6" cy="6" r="1.2" fill="#D4A84A"/>
          <rect x="5.3" y="0"  width="1.4" height="2.2" fill="#D4A84A"/>
          <rect x="5.3" y="9.8" width="1.4" height="2.2" fill="#D4A84A"/>
          <rect x="0"   y="5.3" width="2.2" height="1.4" fill="#D4A84A"/>
          <rect x="9.8" y="5.3" width="2.2" height="1.4" fill="#D4A84A"/>
        </g>""",
}


# ---- Main renderer -------------------------------------------------------


def mascot_svg(tool_name: str, size: int = 96, title: bool = False) -> str:
    """Render a mascot SVG for the given tool name.

    Deterministic: the same `tool_name` always produces the same mascot.
    Returns a complete standalone <svg> string; safe to drop into any HTML.
    """
    s = style_for(tool_name)

    # Body: translucent teal with a slight glow. Two layers (outer soft /
    # inner bright) mimic the brand's bioluminescent jellyfish look.
    body_bright = f"hsl({s.body_hue}, 60%, 82%)"
    body_mid    = f"hsl({s.body_hue}, 48%, 68%)"
    body_edge   = f"hsl({s.body_hue}, 55%, 40%)"
    orb_color   = f"hsl({s.orb_hue}, 80%, 68%)"
    orb_glow    = f"hsl({s.orb_hue}, 90%, 85%)"

    # Halo stays constant across mascots — it's a brand mark, not variation.
    halo = "#D4A84A"
    halo_glow = "#E8C86A"

    # Small gradient id collision-safety so multiple mascots on one page
    # don't share defs.
    uid = hashlib.md5(tool_name.encode()).hexdigest()[:8]
    accessory = ACCESSORIES.get(s.accessory, ACCESSORIES["gear"])

    # Bubble positions — 4 internal bubbles derived from the hash.
    bh = _hash_ints(tool_name, 12)
    bubbles_svg = ""
    for i in range(4):
        # Position inside the body circle (center 50,55, radius 24).
        angle = (bh[i + 5] / 255.0) * 6.283
        r = 6 + (bh[i + 6] % 14)  # 6..20
        bx = 50 + r * _cos(angle)
        by = 55 + r * _sin(angle)
        br = 1 + (bh[i + 7] % 3)  # 1..3 px
        bubbles_svg += (
            f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{br}" '
            f'fill="{body_bright}" opacity="0.55"/>'
            f'<circle cx="{bx - 0.5:.1f}" cy="{by - 0.5:.1f}" r="{br/3:.1f}" '
            f'fill="#FFFFFF" opacity="0.7"/>'
        )

    # Side gear rotation offset per mascot to add slight visual variety.
    gear_rot_l = (bh[8] % 60) - 30
    gear_rot_r = (bh[9] % 60) - 30

    # Smile: wide grin with a tiny teeth line. Curve varies 1..4.
    grin = f"""
      <path d="M42,63 Q50,{66 + s.smile_curve} 58,63 L56,60 Q50,63 44,60 Z"
            fill="#091520"/>
      <path d="M44,60 Q50,63 56,60" stroke="#E8F5F0" stroke-width="0.6"
            fill="none"/>
    """

    title_elem = (
        f'<title>PlanckBot for {_escape(tool_name)} — '
        f'accessory: {s.accessory}</title>' if title else ""
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"
        width="{size}" height="{size}"
        role="img" aria-label="PlanckBot mascot for {_escape(tool_name)}">
      {title_elem}
      <defs>
        <radialGradient id="bodyg-{uid}" cx="42%" cy="34%" r="68%">
          <stop offset="0%"   stop-color="{body_bright}" stop-opacity="0.95"/>
          <stop offset="55%"  stop-color="{body_mid}"    stop-opacity="0.85"/>
          <stop offset="100%" stop-color="{body_edge}"   stop-opacity="0.95"/>
        </radialGradient>
        <radialGradient id="orbg-{uid}" cx="40%" cy="40%" r="60%">
          <stop offset="0%"   stop-color="{orb_glow}"/>
          <stop offset="60%"  stop-color="{orb_color}"/>
          <stop offset="100%" stop-color="{orb_color}" stop-opacity="0.15"/>
        </radialGradient>
        <radialGradient id="eyehl-{uid}" cx="30%" cy="30%" r="55%">
          <stop offset="0%"  stop-color="#A8E8FF"/>
          <stop offset="70%" stop-color="#2E7DB8"/>
          <stop offset="100%" stop-color="#0A3250"/>
        </radialGradient>
        <filter id="glow-{uid}" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="1.4"/>
        </filter>
      </defs>

      <!-- antennae -->
      <path d="M40,28 Q33,14 27,9" stroke="{body_edge}" stroke-width="1.6"
            fill="none" stroke-linecap="round"/>
      <path d="M60,28 Q67,14 73,9" stroke="{body_edge}" stroke-width="1.6"
            fill="none" stroke-linecap="round"/>
      <!-- antenna orb glow halos -->
      <circle cx="27" cy="9" r="6" fill="{orb_color}" opacity="0.3"
              filter="url(#glow-{uid})"/>
      <circle cx="73" cy="9" r="6" fill="{orb_color}" opacity="0.3"
              filter="url(#glow-{uid})"/>
      <!-- antenna orbs -->
      <circle cx="27" cy="9" r="3.2" fill="url(#orbg-{uid})"/>
      <circle cx="73" cy="9" r="3.2" fill="url(#orbg-{uid})"/>

      <!-- brass halo above the head -->
      <ellipse cx="50" cy="21" rx="13" ry="2.6" fill="none"
               stroke="{halo}" stroke-width="2.2"/>
      <ellipse cx="50" cy="21" rx="13" ry="2.6" fill="none"
               stroke="{halo_glow}" stroke-width="0.8" opacity="0.85"
               filter="url(#glow-{uid})"/>

      <!-- body (round, translucent, with subtle outer glow) -->
      <circle cx="50" cy="55" r="26" fill="{body_bright}" opacity="0.12"/>
      <circle cx="50" cy="55" r="24" fill="url(#bodyg-{uid})"/>

      <!-- side brass gears (constant anchor of the brand) -->
      <g transform="translate(22,55) rotate({gear_rot_l})" opacity="0.95">
        <circle r="4.5" fill="none" stroke="{halo}" stroke-width="1.3"/>
        <circle r="1.4" fill="{halo}"/>
        <rect x="-0.8" y="-6"  width="1.6" height="2.5" fill="{halo}"/>
        <rect x="-0.8" y="3.5" width="1.6" height="2.5" fill="{halo}"/>
        <rect x="-6"   y="-0.8" width="2.5" height="1.6" fill="{halo}"/>
        <rect x="3.5"  y="-0.8" width="2.5" height="1.6" fill="{halo}"/>
      </g>
      <g transform="translate(78,55) rotate({gear_rot_r})" opacity="0.95">
        <circle r="4.5" fill="none" stroke="{halo}" stroke-width="1.3"/>
        <circle r="1.4" fill="{halo}"/>
        <rect x="-0.8" y="-6"  width="1.6" height="2.5" fill="{halo}"/>
        <rect x="-0.8" y="3.5" width="1.6" height="2.5" fill="{halo}"/>
        <rect x="-6"   y="-0.8" width="2.5" height="1.6" fill="{halo}"/>
        <rect x="3.5"  y="-0.8" width="2.5" height="1.6" fill="{halo}"/>
      </g>

      <!-- internal bioluminescent bubbles -->
      {bubbles_svg}

      <!-- eyes: large, bright, cute — the brand signature -->
      <circle cx="40" cy="51" r="7.5" fill="#F5FBF8"/>
      <circle cx="60" cy="51" r="7.5" fill="#F5FBF8"/>
      <circle cx="{40 + s.eye_shine_dx}" cy="52" r="5.2"
              fill="url(#eyehl-{uid})"/>
      <circle cx="{60 + s.eye_shine_dx}" cy="52" r="5.2"
              fill="url(#eyehl-{uid})"/>
      <!-- pupil -->
      <circle cx="{40 + s.eye_shine_dx}" cy="52.5" r="2.4" fill="#091520"/>
      <circle cx="{60 + s.eye_shine_dx}" cy="52.5" r="2.4" fill="#091520"/>
      <!-- catchlight -->
      <circle cx="{41 + s.eye_shine_dx}" cy="50.4" r="1.3" fill="#FFFFFF"/>
      <circle cx="{61 + s.eye_shine_dx}" cy="50.4" r="1.3" fill="#FFFFFF"/>
      <circle cx="{38 + s.eye_shine_dx}" cy="53.6" r="0.7" fill="#FFFFFF"
              opacity="0.7"/>
      <circle cx="{58 + s.eye_shine_dx}" cy="53.6" r="0.7" fill="#FFFFFF"
              opacity="0.7"/>

      <!-- cheeks: subtle blush -->
      <circle cx="33" cy="60" r="2.4" fill="#E06B8B" opacity="0.45"/>
      <circle cx="67" cy="60" r="2.4" fill="#E06B8B" opacity="0.45"/>

      <!-- open grin -->
      {grin}

      <!-- accessory (per-tool, in lower-right) -->
      {accessory}
    </svg>"""


# ---- tiny math helpers (avoid pulling in `math` module just for sin/cos) --


def _cos(x: float) -> float:
    import math
    return math.cos(x)


def _sin(x: float) -> float:
    import math
    return math.sin(x)


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;")
         .replace(">", "&gt;").replace('"', "&quot;")
    )
