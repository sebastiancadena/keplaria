#!/usr/bin/env python3
"""Assemble docs/architecture/orientation.svg -- the fleet-and-payload figure.

One picture, four surfaces (console home, /fleet, keplaria.com, README),
answering the one question a cold reader asks first: what is the fleet, what
is a payload, and how do the two relate. The fleet is drawn as a strip
(departments -> agents -> commands); the payload is one amber trajectory
leaving that strip and running along the five lifecycle stops, each stop
marked with how many agents the fleet engaged there (2 / 0 / 0 / 1), which
is the same pattern the submission video's route strip shows.

    uv run python docs/architecture/build_orientation.py

Helpers and palette are copied from build_judge_diagram.py rather than
imported: that module assembles its document at import time.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

HERE = Path(__file__).parent
ASSETS = HERE / "assets"
OUT = Path(os.environ.get("KEPLARIA_ORIENTATION_OUT", HERE / "orientation.svg"))
CONSOLE_COPY = HERE.parent.parent / "console" / "static" / "orientation.svg"

W, H = 1200, 520

VOID = "#0B1020"
AMBER = "#F59E0B"
AMBER_BRIGHT = "#FBBF24"
STAR = "#F8FAFC"
MUTED = "#64748B"

PANEL_FILL = "rgba(248,250,252,0.030)"
PANEL_STROKE = "rgba(100,116,139,0.32)"
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


def text(x, y, s, size=20, family=INTER, weight=400, fill=STAR,
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


def edge(pts, color=AMBER, sw=3, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    p = " ".join(f"{x},{y}" for x, y in pts)
    return (
        f'<polyline points="{p}" fill="none" stroke="{color}"'
        f' stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"'
        f'{d} marker-end="url(#arrow)"/>'
    )


def chip(x, y, w, h, label, family=MONO, size=20):
    return (
        rrect(x, y, w, h, 8, CHIP_FILL, CHIP_STROKE, 1)
        + text(x + w / 2, y + h / 2 + size * 0.35, label, size, family, 500,
               AMBER_BRIGHT, "middle")
    )


def cell(cx, cy, filled: bool) -> str:
    if filled:
        return (f'<circle cx="{cx}" cy="{cy}" r="7" fill="{AMBER}"/>'
                f'<circle cx="{cx}" cy="{cy}" r="11" fill="{AMBER}" fill-opacity="0.14"/>')
    return f'<circle cx="{cx}" cy="{cy}" r="7" fill="none" stroke="{STAR}" stroke-opacity="0.45"/>'


S: list[str] = [
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"'
    f' width="{W}" height="{H}" role="img"'
    ' aria-label="The fleet is the crew and its rulebook: departments,'
    ' agents and commands. A payload is one supplier\'s case carried'
    ' through that fleet for months.">',
    "<style>",
    font_face(SG, "SpaceGrotesk.woff2"),
    font_face(INTER, "Inter.woff2"),
    font_face(MONO, "JetBrainsMono.woff2"),
    "text{white-space:pre}",
    "</style>",
    "<defs>",
    '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4"'
    ' markerHeight="4" orient="auto-start-reverse">'
    f'<path d="M0,0 L10,5 L0,10 z" fill="{AMBER}"/></marker>',
    "</defs>",
    f'<rect width="{W}" height="{H}" fill="{VOID}"/>',
]

# ------------------------------------------------------------ the fleet
S.append(rrect(24, 24, 1152, 300))
S.append(text(48, 66, "THE FLEET", 26, SG, 600, STAR, spacing="0.04em"))
S.append(text(196, 66, "the crew and its rulebook", 20, INTER, 400, MUTED))

for x, label in [(48, "DEPARTMENTS"), (440, "AGENTS"), (860, "COMMANDS")]:
    S.append(text(x, 108, label, 16, INTER, 600, MUTED, spacing="0.08em"))

DEPTS = [("procurement", True), ("compliance", True), ("finance", False)]
for i, (name, in_scope) in enumerate(DEPTS):
    y = 152 + i * 46
    S.append(text(48, y + 7, name, 22, MONO, 500, STAR))
    S.append(cell(262, y, in_scope))
    S.append(cell(288, y, in_scope))
# Two lines: on one line this note ran to x~438 and collided with the agents
# column's note at x=440 (found 2026-08-24).
S.append(text(48, 292, "finance engages no agents", 16, INTER, 400, MUTED))
S.append(text(48, 312, "and issues no command", 16, INTER, 400, MUTED))

S.append(edge([(322, 198), (424, 198)]))

S.append(chip(440, 128, 200, 44, "coordinator"))
S.append(text(652, 158, "proposes a route", 18, INTER, 400, MUTED))
S.append(chip(440, 190, 140, 44, "evidence"))
S.append(chip(592, 190, 170, 44, "compliance"))
S.append(text(440, 268, "policy decides which may run;", 18, INTER, 500, AMBER_BRIGHT))
S.append(text(440, 292, "an out-of-scope proposal is refused and recorded",
              16, INTER, 400, MUTED))

S.append(edge([(780, 198), (846, 198)]))

for i, cmd in enumerate(["create_supplier", "attach_evidence",
                         "request_renewal", "apply_hold", "clear_hold"]):
    S.append(text(860, 148 + i * 30, cmd, 18, MONO, 500, STAR))
S.append(text(860, 300, "the only way anything reaches the ERP",
              16, INTER, 400, MUTED))

# ---------------------------------------------------------- one payload
S.append(text(48, 372, "ONE PAYLOAD", 26, SG, 600, STAR, spacing="0.04em"))
S.append(text(270, 372, "one supplier's case, carried for months",
              20, INTER, 400, MUTED))

# The trajectory leaves the fleet strip and settles onto the lifecycle line,
# staying clear of the section heading's text row (y=372) on its way down.
S.append(f'<path d="M 850,324 C 850,410 300,424 80,424" fill="none"'
         f' stroke="{AMBER}" stroke-width="3" stroke-dasharray="8 8"/>')

STOPS = [
    (80, "Onboarded", "2 agents"),
    (300, "Active", ""),
    (540, "Renewal requested", "clock · 0 agents"),
    (800, "Purchasing held", "clock · 0 agents"),
    (1060, "Hold released", "1 agent"),
]
for (x, _, _), (nx, _, _) in zip(STOPS, STOPS[1:]):
    S.append(edge([(x + 12, 440), (nx - 14, 440)]))
for x, label, count in STOPS:
    S.append(f'<circle cx="{x}" cy="440" r="9" fill="{AMBER}"/>')
    S.append(text(x, 476, label, 20, INTER, 500, STAR, "middle"))
    if count:
        S.append(text(x, 502, count, 18, MONO, 500, AMBER_BRIGHT, "middle"))

S.append(text(1176, 372, "Counts: agents the fleet engaged at that stop.",
              16, INTER, 400, MUTED, "end"))
S.append(text(1176, 394, "Clock events engage none; policy alone acts.",
              16, INTER, 400, MUTED, "end"))

S.append("</svg>")
OUT.write_text("\n".join(S))
if "KEPLARIA_ORIENTATION_OUT" not in os.environ:
    CONSOLE_COPY.write_bytes(OUT.read_bytes())
print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB)")
