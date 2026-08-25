#!/usr/bin/env python3
"""Assemble docs/architecture/judge-diagram.svg — the six-box video diagram.

A judge sees this for about twenty seconds at 2:25 of the submission video,
on a 1920x1080 frame that is also this canvas. It is NOT a smaller version of
architecture.svg: that one is a poster you lean into, with 10-13px labels.
Here nothing carrying meaning sits below 28px, and a label that will not fit
is cut rather than shrunk.

    uv run python docs/architecture/build_judge_diagram.py

The helpers and palette below are deliberate copies of the ones in build.py
rather than a shared import. build.py assembles its document at module level,
so importing it writes a file as a side effect, and refactoring a generator
whose byte output is checked is not a thing to do for a second diagram.

THE ONE DEVICE: two walls, each pierced exactly once. The Policy Gate is
embedded in the first; a single heavy arrow crosses the second. That makes
"only the gate decides" and "only the outbox reaches the ERP" read as shape
instead of as caption, which is the whole reason this diagram exists.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

HERE = Path(__file__).parent
ASSETS = HERE / "assets"
OUT = Path(os.environ.get("KEPLARIA_JUDGE_DIAGRAM_OUT", HERE / "judge-diagram.svg"))

W, H = 1920, 1080

VOID = "#0B1020"
INK = "#111827"
AMBER = "#F59E0B"
AMBER_BRIGHT = "#FBBF24"
STAR = "#F8FAFC"
MUTED = "#64748B"

PANEL_FILL = "rgba(248,250,252,0.030)"
PANEL_STROKE = "rgba(100,116,139,0.32)"
DOOR_STROKE = "rgba(100,116,139,0.55)"
CHIP_FILL = "rgba(17,24,39,0.92)"
CHIP_STROKE = "rgba(100,116,139,0.55)"

SG = "Space Grotesk"
INTER = "Inter"
MONO = "JetBrains Mono"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def font_face(family: str, fname: str) -> str:
    data = base64.b64encode((ASSETS / fname).read_bytes()).decode()
    return (
        f"@font-face{{font-family:'{family}';"
        f"src:url(data:font/woff2;base64,{data}) format('woff2');"
        f"font-weight:100 900;font-style:normal;}}"
    )


def lockup_symbol() -> str:
    """The real mark from the brand repo, embedded verbatim.

    Geometry untouched per the brand guidelines -- the ellipse's off-centre
    focus is the whole idea of the mark, and a redrawn or letter-spaced
    substitute throws it away.
    """
    raw = (ASSETS / "keplaria-lockup-horizontal-dark.svg").read_text()
    vb = re.search(r'viewBox="([^"]+)"', raw).group(1)
    body = re.sub(r"^<svg[^>]*>", "", raw.strip())
    body = re.sub(r"</svg>\s*$", "", body)
    return f'<symbol id="keplaria-lockup" viewBox="{vb}">{body}</symbol>'


def text(x, y, s, size=28, family=INTER, weight=400, fill=STAR,
         anchor="start", spacing=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}"'
        f' font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}>'
        f"{esc(s)}</text>"
    )


def rrect(x, y, w, h, r=12, fill=PANEL_FILL, stroke=PANEL_STROKE, sw=1.5):
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"'
        f' fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    )


def edge(pts, color=AMBER, sw=4, dash=None, marker="amber"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    p = " ".join(f"{x},{y}" for x, y in pts)
    return (
        f'<polyline points="{p}" fill="none" stroke="{color}"'
        f' stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"'
        f'{d} marker-end="url(#arrow-{marker})"/>'
    )


def chip(x, y, w, h, label, family=INTER, size=28):
    return (
        rrect(x, y, w, h, 8, CHIP_FILL, CHIP_STROKE, 1)
        + text(x + w / 2, y + h / 2 + size * 0.35, label, size, family, 500,
               STAR, "middle")
    )


S: list[str] = [
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"'
    f' width="{W}" height="{H}" role="img"'
    ' aria-label="Keplaria: agents propose, a deterministic gate decides,'
    ' and only the outbox reaches the system of record">',
    "<style>",
    font_face(SG, "SpaceGrotesk.woff2"),
    font_face(INTER, "Inter.woff2"),
    font_face(MONO, "JetBrainsMono.woff2"),
    "text{white-space:pre}",
    "</style>",
    "<defs>",
]
for name, color, mw in [("amber", AMBER, 5), ("exit", AMBER, 4.5)]:
    S.append(
        f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="8" refY="5"'
        f' markerWidth="{mw}" markerHeight="{mw}" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
    )
S.append(lockup_symbol())
S.append("</defs>")
S.append(f'<rect width="{W}" height="{H}" fill="{VOID}"/>')

# Half of this element is the lockup's built-in clear space, so 380 wide
# puts the artwork at ~284px -- a title-card mark on a 1920 frame, not
# the 172px speck the first pass drew.
S.append('<use href="#keplaria-lockup" x="44" y="24" width="380" height="192"/>')

# ------------------------------------------------------------------ walls
# Wall 1 breaks where the Policy Gate sits in it; wall 2 breaks only where
# the single exit arrow crosses. Every other stroke stops at these lines.
WALL = 'stroke="%s" stroke-width="4" stroke-opacity="0.75"' % MUTED
for y0, y1 in [(240, 320), (560, 1040)]:
    S.append(f'<line x1="915" y1="{y0}" x2="915" y2="{y1}" {WALL}/>')
for y0, y1 in [(240, 415), (465, 1040)]:
    S.append(f'<line x1="1750" y1="{y0}" x2="1750" y2="{y1}" {WALL}/>')

for zx, label in [(370, "PROPOSE"), (915, "DECIDE"), (1335, "ACT"),
                  (1828, "RECORD")]:
    S.append(text(zx, 215, label, 30, SG, 500, MUTED, "middle",
                  spacing="0.18em"))

# ------------------------------------------------------------------ boxes
S.append(rrect(60, 330, 240, 220))
S.append(text(84, 392, "Triggers", 40, SG, 600, STAR))
for i, line in enumerate(["supplier packet", "clock event", "certificate"]):
    S.append(text(84, 446 + i * 40, line, 28, INTER, 400, MUTED))

S.append(rrect(360, 360, 340, 160))
S.append(text(384, 428, "Coordinator", 40, SG, 600, STAR))
S.append(text(384, 476, "proposes — never acts", 28, INTER, 400, MUTED))

S.append(rrect(750, 320, 352, 240, 14, PANEL_FILL, DOOR_STROKE, 2))
S.append(text(764, 398, "Policy Gate", 48, SG, 600, STAR))
S.append(text(764, 448, "approve · park · refuse", 28, INTER, 400, MUTED))
S.append(chip(764, 480, 200, 48, "fleet.v1", MONO))

# The security boundary is drawn where the narration speaks it (beat 3):
# the specialists carry no credential, the executor is the one scoped
# identity and the sole write path, and Ground Control sits behind IAP.
S.append(rrect(1160, 340, 270, 280))
S.append(text(1184, 402, "Specialists", 40, SG, 600, STAR))
S.append(chip(1184, 424, 222, 48, "evidence"))
S.append(chip(1184, 480, 222, 48, "compliance"))
S.append(text(1184, 566, "no ERP credential", 28, INTER, 400, MUTED))
S.append(text(1184, 600, "no write tools", 28, INTER, 400, MUTED))

S.append(rrect(1060, 740, 380, 180))
S.append(text(1090, 812, "Ground Control", 40, SG, 600, STAR))
S.append(text(1090, 860, "human approval · Cloud IAP", 28, INTER, 400, MUTED))

S.append(rrect(1490, 340, 240, 250, 14, PANEL_FILL, DOOR_STROKE, 2))
S.append(text(1515, 412, "Outbox", 40, SG, 600, STAR))
S.append(chip(1515, 444, 170, 48, "executor", MONO))
S.append(text(1515, 530, "scoped ERP role", 28, INTER, 400, MUTED))
S.append(text(1515, 564, "sole write path", 28, INTER, 400, MUTED))

S.append(rrect(1790, 360, 105, 160, 12, INK, CHIP_STROKE, 2))
S.append(text(1842, 454, "ERP", 40, SG, 600, STAR, "middle"))

# ------------------------------------------------------------------ edges
# Amber is the case's path and nothing else on this canvas.
S.append(edge([(300, 440), (352, 440)]))
S.append(edge([(700, 440), (742, 440)]))
S.append(edge([(1102, 440), (1152, 440)]))
S.append(edge([(1430, 440), (1482, 440)]))
S.append(edge([(1000, 560), (1000, 800), (1052, 800)], dash="12 10"))
S.append(text(1022, 690, "parked", 28, INTER, 500, AMBER_BRIGHT))
S.append(edge([(1440, 830), (1600, 830), (1600, 598)]))
S.append(text(1615, 720, "released", 28, INTER, 500, AMBER_BRIGHT))
S.append(edge([(1730, 440), (1782, 440)], sw=6, marker="exit"))

S.append("</svg>")
OUT.write_text("\n".join(S))
print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB)")
