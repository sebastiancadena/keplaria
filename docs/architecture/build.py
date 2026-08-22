#!/usr/bin/env python3
"""Assemble docs/architecture/architecture.svg from the sources in assets/.

The diagram is hand-laid-out: every coordinate below is a deliberate design
decision, not autolayout. Regenerate with:

    uv run python docs/architecture/build.py

Inputs (all committed under assets/):
  - three OFL font subsets (woff2, variable weight axes preserved)
  - official Google Cloud icons (per-icon CSS classes and mask ids are
    namespaced here so they can share one document)
  - the Keplaria lockup from the keplaria-assets repo (canonical geometry,
    embedded verbatim)

Brand rules honoured (keplaria-assets/docs/brand-guidelines.md): Void ground,
amber only as accent, Space Grotesk never below 18px, Inter for labels,
JetBrains Mono for identifiers, lockup geometry untouched.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

HERE = Path(__file__).parent
ASSETS = HERE / "assets"
# KEPLARIA_DIAGRAM_OUT lets doctor.sh rebuild to a temp path and byte-compare
# against the committed SVG without touching the working tree.
OUT = Path(os.environ.get("KEPLARIA_DIAGRAM_OUT", HERE / "architecture.svg"))

# ---------------------------------------------------------------- palette
VOID = "#0B1020"
INK = "#111827"
AMBER = "#F59E0B"
AMBER_BRIGHT = "#FBBF24"
STAR = "#F8FAFC"
MUTED = "#64748B"

PANEL_FILL = "rgba(248,250,252,0.030)"
PANEL_STROKE = "rgba(100,116,139,0.32)"
GROUP_FILL = "rgba(248,250,252,0.045)"
GROUP_STROKE = "rgba(100,116,139,0.50)"
CHIP_FILL = "rgba(17,24,39,0.92)"
CHIP_STROKE = "rgba(100,116,139,0.55)"

SG = "Space Grotesk"
INTER = "Inter"
MONO = "JetBrains Mono"

W, H = 1920, 1080

# ---------------------------------------------------------------- helpers


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def font_face(family: str, fname: str) -> str:
    data = base64.b64encode((ASSETS / fname).read_bytes()).decode()
    return (
        f"@font-face{{font-family:'{family}';"
        f"src:url(data:font/woff2;base64,{data}) format('woff2');"
        f"font-weight:100 900;font-style:normal;}}"
    )


def load_icon(slug: str) -> str:
    """Return a <symbol> wrapping one GCP icon, with classes/ids namespaced."""
    raw = (ASSETS / f"{slug}.svg").read_text()
    vb = re.search(r'viewBox="([^"]+)"', raw).group(1)
    body = re.sub(r"^<svg[^>]*>", "", raw.strip())
    body = re.sub(r"</svg>\s*$", "", body)
    safe = re.sub(r"[^a-z0-9]", "", slug)
    body = body.replace("cls-", f"{safe}-c-")
    for m in sorted(set(re.findall(r'id="([^"]+)"', body))):
        body = body.replace(f'id="{m}"', f'id="{safe}-{m}"')
        body = body.replace(f"url(#{m})", f"url(#{safe}-{m})")
        body = body.replace(f'href="#{m}"', f'href="#{safe}-{m}"')
    return f'<symbol id="icon-{safe}" viewBox="{vb}">{body}</symbol>'


def lockup_symbol() -> str:
    raw = (ASSETS / "keplaria-lockup-horizontal-dark.svg").read_text()
    vb = re.search(r'viewBox="([^"]+)"', raw).group(1)
    body = re.sub(r"^<svg[^>]*>", "", raw.strip())
    body = re.sub(r"</svg>\s*$", "", body)
    return f'<symbol id="keplaria-lockup" viewBox="{vb}">{body}</symbol>'


def text(
    x: float,
    y: float,
    s: str,
    size: float = 12,
    family: str = INTER,
    weight: int = 400,
    fill: str = STAR,
    anchor: str = "start",
    spacing: str | None = None,
    halo: bool = False,
) -> str:
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    h = (
        f' stroke="{VOID}" stroke-width="5" paint-order="stroke"'
        ' stroke-linejoin="round"'
        if halo
        else ""
    )
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}"'
        f' font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}{h}>'
        f"{esc(s)}</text>"
    )


def rrect(
    x: float,
    y: float,
    w: float,
    h: float,
    r: float = 10,
    fill: str = CHIP_FILL,
    stroke: str = CHIP_STROKE,
    sw: float = 1,
    dash: str | None = None,
) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"'
        f' fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'
    )


def badge(x: float, y: float, n: int) -> str:
    """Numbered enforcement-point marker."""
    return (
        f'<circle cx="{x}" cy="{y}" r="9.5" fill="{AMBER_BRIGHT}"'
        f' stroke="{VOID}" stroke-width="1.5"/>'
        + text(x, y + 3.8, str(n), 11.5, INTER, 700, VOID, "middle")
    )


def spark(cx: float, cy: float, r: float = 5.5) -> str:
    """Four-point star: marks a node that calls Gemini."""
    k = r * 0.28
    return (
        f'<path d="M{cx},{cy - r} L{cx + k},{cy - k} L{cx + r},{cy}'
        f" L{cx + k},{cy + k} L{cx},{cy + r} L{cx - k},{cy + k}"
        f' L{cx - r},{cy} L{cx - k},{cy - k} Z" fill="{AMBER_BRIGHT}"/>'
    )


def no_egress(cx: float, cy: float) -> str:
    return (
        f'<circle cx="{cx}" cy="{cy}" r="6" fill="none"'
        f' stroke="{AMBER_BRIGHT}" stroke-width="1.6"/>'
        f'<line x1="{cx - 4.2}" y1="{cy + 4.2}" x2="{cx + 4.2}" y2="{cy - 4.2}"'
        f' stroke="{AMBER_BRIGHT}" stroke-width="1.6"/>'
    )


def edge(
    pts: list[tuple[float, float]],
    color: str = MUTED,
    sw: float = 1.6,
    dash: str | None = None,
    marker: str = "muted",
) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    p = " ".join(f"{x},{y}" for x, y in pts)
    return (
        f'<polyline points="{p}" fill="none" stroke="{color}"'
        f' stroke-width="{sw}"{d} marker-end="url(#arrow-{marker})"/>'
    )


def chip(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    subs: list[tuple[str, str]] | None = None,
    icon: str | None = None,
    badge_n: int | None = None,
) -> str:
    out = [rrect(x, y, w, h)]
    tx = x + 14
    if icon:
        isz = 26
        out.append(
            f'<use href="#icon-{icon}" x="{x + 12}" y="{y + (h - isz) / 2}"'
            f' width="{isz}" height="{isz}"/>'
        )
        tx = x + 12 + isz + 10
    ty = y + 21 if subs else y + h / 2 + 4.5
    out.append(text(tx, ty, title, 13.5, INTER, 600, STAR))
    if subs:
        ly = ty + 16
        for s, style in subs:
            fam = MONO if style == "mono" else INTER
            sz = 10.5 if style == "mono" else 11
            out.append(text(tx, ly, s, sz, fam, 400, MUTED))
            ly += 14.5
    if badge_n is not None:
        out.append(badge(x + w - 2, y + 2, badge_n))
    return "".join(out)


def pill(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    sub: str | None = None,
    tag: str | None = None,
    badge_n: int | None = None,
    sparked: bool = False,
) -> str:
    out = [rrect(x, y, w, h, r=8)]
    cy = y + (17 if sub else h / 2 + 4)
    out.append(text(x + w / 2, cy, title, 12, INTER, 600, STAR, "middle"))
    if sub:
        out.append(text(x + w / 2, y + 31, sub, 10.5, INTER, 400, MUTED, "middle"))
    if tag:
        out.append(
            text(x + w / 2, y - 6, tag, 10, MONO, 500, AMBER_BRIGHT, "middle", "0.03em")
        )
    if sparked:
        out.append(spark(x + 12, y + 10))
    if badge_n is not None:
        out.append(badge(x + w - 2, y + 2, badge_n))
    return "".join(out)


def band(x, y, w, h, title, desc):
    return (
        rrect(x, y, w, h, r=14, fill=PANEL_FILL, stroke=PANEL_STROKE)
        + text(x + 20, y + 26, title, 18, SG, 600, STAR, spacing="0.06em")
        + text(x + w - 28, y + 26, desc, 11.5, INTER, 400, MUTED, "end")
    )


def sidebox(x, y, w, h, title):
    return rrect(x, y, w, h, r=14, fill=PANEL_FILL, stroke=PANEL_STROKE) + text(
        x + 20, y + 27, title, 18, SG, 600, STAR, spacing="0.04em"
    )


# ---------------------------------------------------------------- document
S: list[str] = []
S.append(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"'
    f' width="{W}" height="{H}" role="img"'
    ' aria-label="Keplaria system architecture">'
)
S.append("<style>")
S.append(font_face(SG, "SpaceGrotesk.woff2"))
S.append(font_face(INTER, "Inter.woff2"))
S.append(font_face(MONO, "JetBrainsMono.woff2"))
S.append("text{white-space:pre}")
S.append("</style>")

S.append("<defs>")
for slug in [
    "pubsub",
    "cloud_run",
    "cloud_scheduler",
    "firestore",
    "secret_manager",
    "trace",
    "compute_engine",
    "vertexai",
    "identity-aware_proxy",
]:
    S.append(load_icon(slug))
S.append(lockup_symbol())
for name, color in [("amber", AMBER), ("muted", MUTED), ("star", STAR)]:
    S.append(
        f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="8" refY="5"'
        ' markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
    )
S.append("</defs>")

S.append(f'<rect width="{W}" height="{H}" fill="{VOID}"/>')

# ---------------------------------------------------------------- header
S.append('<use href="#keplaria-lockup" x="6" y="12" width="166" height="84"/>')
S.append(text(196, 56, "System architecture", 28, SG, 700, STAR, spacing="-0.015em"))
S.append(
    text(
        196,
        80,
        "Continuous supplier assurance — onboard once, govern for months",
        13,
        INTER,
        400,
        MUTED,
    )
)
S.append(
    text(
        1896,
        50,
        "Google Cloud · us-central1 · project keplaria",
        12,
        MONO,
        400,
        MUTED,
        "end",
    )
)
S.append(
    text(
        1896,
        70,
        "Gemini 3.6 Flash · ADK 2 · Agent Runtime · Cloud Run · Pub/Sub · Firestore",
        12,
        MONO,
        400,
        MUTED,
        "end",
    )
)

# ------------------------------------------------------ plane geometry
PX, PW = 24, 1376  # planes column: x 24..1400
SX, SW_ = 1416, 480  # sidebar column: x 1416..1896

A_Y, A_H = 112, 150
B_Y, B_H = 274, 396
C_Y, C_H = 682, 140
D_Y, D_H = 834, 132
E_Y, E_H = 978, 86

# ================================================================ EVENTS
S.append(
    band(PX, A_Y, PW, A_H, "EVENTS", "the spine — versioned envelopes, trusted clock, dead-lettering")
)
S.append(
    chip(
        56, 152, 300, 48,
        "Cloud Scheduler",
        [("lifecycle clock · command sweep */15", "sub")],
        icon="cloud_scheduler",
    )
)
S.append(
    chip(
        56, 206, 300, 48,
        "Demo harness",
        [("separate topic + identity · disclosed", "sub")],
    )
)
S.append(
    chip(
        430, 156, 260, 84,
        "Pub/Sub topic",
        [("keplaria-events", "mono"), ("versioned · at-least-once", "sub")],
        icon="pubsub",
    )
)
S.append(
    chip(
        750, 156, 270, 84,
        "Push subscription",
        [("keplaria-events-push", "mono"), ("OIDC identity · retry 60–600 s", "sub")],
        badge_n=1,
    )
)
S.append(
    chip(
        1080, 156, 290, 84,
        "Dead-letter topic",
        [("keplaria-events-dead", "mono"), ("5 deliveries → durable dead_events", "sub")],
        icon="pubsub",
    )
)
S.append(edge([(356, 176), (424, 190)], AMBER, 2.4, marker="amber"))
S.append(edge([(356, 230), (424, 206)], AMBER, 2.4, marker="amber"))
S.append(edge([(690, 198), (744, 198)], AMBER, 2.4, marker="amber"))
S.append(edge([(1020, 198), (1074, 198)], MUTED, 1.6, dash="5 4"))
S.append(text(1047, 188, "undeliverable", 10, INTER, 400, MUTED, "middle", halo=True))

# spine down to the ingress + sweep line
S.append(edge([(885, 240), (885, 268), (380, 268), (380, 312)], AMBER, 2.4, marker="amber"))
S.append(text(630, 262, "authenticated push", 10.5, INTER, 500, AMBER_BRIGHT, "middle", halo=True))
S.append(edge([(56, 176), (32, 176), (32, 340), (38, 340)], MUTED, 1.6))
S.append(text(40, 271, "/admin/sweep · */15", 10, MONO, 400, MUTED, halo=True))

# ===================================================== MISSION EXECUTION
S.append(
    band(
        PX, B_Y, PW, B_H,
        "MISSION EXECUTION",
        "backend — a deterministic adapter, an agent graph, one durable truth",
    )
)

# --- Cloud Run ingress group
S.append(rrect(40, 316, 400, 330, r=12, fill=GROUP_FILL, stroke=GROUP_STROKE))
S.append('<use href="#icon-cloud_run" x="54" y="328" width="24" height="24"/>')
S.append(text(88, 345, "Cloud Run — keplaria-ingress", 14, INTER, 600, STAR))
S.append(text(56, 364, "private · OIDC callers only · concurrency=1 · own identity", 10.5, MONO, 400, MUTED))
S.append(
    chip(
        56, 376, 368, 64,
        "Inbox transaction",
        [
            ("claims event_id — duplicates ignored", "sub"),
            ("creates / advances the case, bumps case_version", "sub"),
        ],
        badge_n=2,
    )
)
S.append(
    chip(
        56, 452, 368, 104,
        "ERP executor — deterministic code",
        [
            ("drains the command outbox after every engine run", "sub"),
            ("idempotent · bounded retry ≤5 → terminal dead", "sub"),
            ("refuses permissive commands unless clear / approved", "sub"),
            ("runs here because the engine has no internet path", "sub"),
        ],
        badge_n=8,
    )
)

# --- Firestore
S.append(
    chip(
        480, 460, 300, 132,
        "Firestore — the case's truth",
        [
            ("cases · inbox · outbox · approvals", "mono"),
            ("decision_ledger · dead_events", "mono"),
            ("transactions own case_version", "sub"),
            ("exactly-once effects, idempotent replay", "sub"),
        ],
        icon="firestore",
    )
)

# --- Agent Runtime group
S.append(rrect(820, 316, 550, 330, r=12, fill=GROUP_FILL, stroke=GROUP_STROKE))
S.append('<use href="#icon-vertexai" x="834" y="328" width="24" height="24"/>')
S.append(text(868, 345, "Agent Runtime — the mission graph", 14, INTER, 600, STAR))
S.append(text(868, 358, "reasoning engine keplaria · auto-registered in Agent Registry", 10.5, MONO, 400, MUTED))
S.append(no_egress(1128, 341))
S.append(text(1140, 345, "no public-internet egress — by design", 11, INTER, 500, AMBER_BRIGHT))

# model-exposure boundary + Sessions
S.append(rrect(828, 364, 350, 52, r=10, fill="rgba(251,191,36,0.05)", stroke=AMBER_BRIGHT, sw=1.2, dash="6 4"))
S.append(spark(846, 383))
S.append(text(860, 388, "Gemini 3.6 Flash — Vertex AI (global)", 12.5, INTER, 600, STAR))
S.append(text(860, 404, "in: redacted copy only — no raw documents, no secrets", 9.8, INTER, 400, MUTED))
S.append(badge(1178, 366, 6))
S.append(
    chip(
        1190, 364, 172, 52,
        "Agent Sessions",
        [("persistent · resumable", "sub")],
    )
)

# pipeline pills — cols x828/1010/1192, w170
S.append(pill(828, 452, 170, 40, "parse + document gate", "extraction · provenance", "Evidence agent", badge_n=3, sparked=True))
S.append(pill(1010, 452, 170, 40, "coordinator", "routing proposal", "Coordinator agent", sparked=True))
S.append(pill(1192, 452, 170, 40, "route validation", "dept scopes · fail-closed", "code, not model", badge_n=4))
S.append(pill(828, 512, 170, 40, "risk gate", "policy v2 · three bands", "code, not model", badge_n=5))
S.append(pill(1010, 512, 170, 40, "compliance analysis", "independently re-checked", "Compliance agent", sparked=True))
S.append(pill(1192, 512, 170, 40, "sanctions screening", "yente fuzzy match", "code · PSC-I"))
S.append(pill(828, 572, 170, 40, "queue ERP command", "band: clear"))
S.append(pill(1010, 572, 170, 40, "park for approval", "band: review"))
S.append(pill(1192, 572, 170, 40, "quarantine — no writes", "band: blocked · bad routes"))

# pipeline arrows
S.append(edge([(999, 472), (1008, 472)], MUTED, 1.6))
S.append(edge([(1181, 472), (1190, 472)], MUTED, 1.6))
S.append(edge([(1277, 492), (1277, 508)], MUTED, 1.6))
S.append(edge([(1192, 532), (1184, 532)], MUTED, 1.6))
S.append(edge([(1010, 532), (1002, 532)], MUTED, 1.6))
S.append(edge([(913, 552), (913, 568)], MUTED, 1.6))
S.append(edge([(930, 552), (1080, 566)], MUTED, 1.6))
S.append(edge([(950, 552), (1262, 562)], MUTED, 1.6))

# --- edges inside the plane
S.append(edge([(424, 408), (640, 408), (640, 432), (814, 432)], AMBER, 2.4, marker="amber"))
S.append(text(530, 400, "invoke graph — 1 concurrent query", 10.5, INTER, 500, AMBER_BRIGHT, "middle", halo=True))
S.append(edge([(820, 520), (786, 520)], MUTED, 1.6))
S.append(edge([(828, 588), (786, 588)], MUTED, 1.6))
S.append(text(806, 580, "outbox", 9.5, INTER, 400, MUTED, "middle", halo=True))
S.append(edge([(476, 520), (428, 520)], MUTED, 1.6))
S.append(text(452, 512, "drain", 9.5, INTER, 400, MUTED, "middle", halo=True))

# =================================================== ENTERPRISE SYSTEMS
S.append(
    band(
        PX, C_Y, PW, C_H,
        "ENTERPRISE SYSTEMS",
        "real systems, scoped identities — the model never touches them",
    )
)
S.append(
    chip(
        120, 720, 340, 76,
        "ERPNext — system of record",
        [
            ("andina-foods on Frappe Cloud · real enterprise UI", "sub"),
            ("scoped identity · native RBAC 403 negative control", "sub"),
        ],
    )
)
S.append(
    chip(
        520, 720, 280, 76,
        "Secret Manager",
        [
            ("scoped Frappe credentials", "sub"),
            ("read at runtime by service identity", "sub"),
        ],
        icon="secret_manager",
    )
)
S.append(
    chip(
        1030, 720, 340, 76,
        "Sanctions screening — yente",
        [
            ("keplaria-yente · 10.10.0.2 · no ingress", "mono"),
            ("synthetic rights-cleared watchlist · ES 9", "sub"),
        ],
        icon="compute_engine",
    )
)

# cross-plane edges touching this band
S.append(edge([(300, 556), (300, 716)], AMBER, 2.4, marker="amber"))
S.append(text(312, 700, "idempotent ERP writes — create · renew · hold · release", 10.5, INTER, 500, AMBER_BRIGHT, halo=True))
S.append(edge([(660, 716), (660, 668), (350, 668), (350, 650)], MUTED, 1.6))
S.append(text(560, 682, "runtime credentials", 9.5, INTER, 400, MUTED, "middle", halo=True))
S.append(edge([(1350, 532), (1390, 532), (1390, 758), (1374, 758)], STAR, 1.6, dash="6 4", marker="star"))
S.append(text(1386, 650, "PSC-I · keplaria-psc2", 10, MONO, 400, STAR, "end", halo=True))

# ====================================================== GROUND CONTROL
S.append(
    band(
        PX, D_Y, PW, D_H,
        "GROUND CONTROL",
        "frontend — humans see everything, decide only what policy requires",
    )
)
S.append(
    chip(
        120, 872, 340, 76,
        "Public console — read-only",
        [
            ("keplaria-console", "mono"),
            ("redacted case visibility — safe for judges", "sub"),
            ("/fleet renders the catalog — departments + agents", "sub"),
        ],
        icon="cloud_run",
    )
)
S.append(
    chip(
        500, 872, 400, 76,
        "Fleet catalog + Agent Registry",
        [
            ("fleet.v1.json — the routing authorization source", "mono"),
            ("versioned agents · per-department agent + command scopes", "sub"),
            ("a finance event engaging agents is refused + recorded", "sub"),
        ],
        icon="vertexai",
    )
)
S.append(
    chip(
        970, 872, 400, 76,
        "Review service — Cloud IAP",
        [
            ("keplaria-review · identity from signed assertion", "mono"),
            ("one decision per approval_id · exact case_version", "sub"),
        ],
        icon="identity-aware_proxy",
    )
)
S.append(badge(1370, 874, 9))
S.append(badge(1370, 946, 7))

# reviewer figure
S.append(f'<circle cx="940" cy="896" r="7" fill="none" stroke="{STAR}" stroke-width="1.6"/>')
S.append(f'<path d="M928,918 a12,9 0 0 1 24,0" fill="none" stroke="{STAR}" stroke-width="1.6"/>')
S.append(text(940, 934, "reviewer", 10, INTER, 400, MUTED, "middle"))
S.append(edge([(954, 904), (966, 904)], MUTED, 1.6))

# park → review, approval → Firestore, console ← Firestore
S.append(edge([(1095, 612), (1095, 656), (990, 656), (990, 868)], AMBER, 2.4, marker="amber"))
S.append(text(998, 676, "review case parks for a human", 10.5, INTER, 500, AMBER_BRIGHT, halo=True))
S.append(edge([(950, 868), (950, 660), (760, 660), (760, 596)], AMBER, 2.4, marker="amber"))
S.append(text(855, 652, "approval commits — exact case_version", 10.5, INTER, 500, AMBER_BRIGHT, "middle", halo=True))
S.append(edge([(630, 596), (630, 644), (488, 644), (488, 895), (464, 895)], MUTED, 1.6))
S.append(text(497, 780, "redacted projections", 9.5, INTER, 400, MUTED, "middle", halo=True))

# ========================================================== TELEMETRY
S.append(
    band(
        PX, E_Y, PW, E_H,
        "TELEMETRY",
        "every decision leaves a trace, sourced from authoritative state",
    )
)
S.append(
    chip(
        120, 1010, 360, 46,
        "Cloud Trace",
        [("OpenTelemetry reasoning traces — engine + ingress", "sub")],
        icon="trace",
    )
)
S.append(
    chip(
        520, 1010, 400, 46,
        "Decision ledger",
        [("every command: machine band + human decision + attempts", "sub")],
        icon="firestore",
    )
)
S.append(
    chip(
        960, 1010, 410, 46,
        "Agent Platform Sessions",
        [("persistent, resumable history across the async window", "sub")],
        icon="vertexai",
    )
)

# ============================================================ SIDEBAR
S.append(sidebox(SX, 112, SW_, 150, "Read it in 20 seconds"))
for i, line in enumerate(
    [
        "An event fires — real clock, or a disclosed demo clock.",
        "Deterministic gates decide; agents only propose.",
        "Risky cases stop for a human; clean ones fly on.",
        "Every write is idempotent; every failure is durable.",
    ]
):
    S.append(text(SX + 20, 160 + i * 22, line, 12, INTER, 400, STAR))

S.append(sidebox(SX, 274, SW_, 396, "Enforcement points"))
ENF = [
    ("OIDC-verified push", "only authenticated Pub/Sub reaches the private ingress"),
    ("Transactional inbox", "duplicate + out-of-order events claimed exactly once"),
    ("Document gate", "a tainted document never reaches agent-readable state"),
    ("Route validation", "the LLM proposes; deterministic policy disposes"),
    ("Risk gate", "versioned policy — clear / review parks / blocked quarantines"),
    ("Model boundary", "documents reach Gemini only as the redacted copy"),
    ("Approval binding", "one decision per approval_id, bound to exact case_version"),
    ("Scoped executor", "separate identity · idempotent · bounded retry → dead"),
    ("IAP identity", "reviewer verified from a signed assertion, not a header"),
]
for i, (t, d) in enumerate(ENF):
    y = 320 + i * 38
    S.append(badge(SX + 30, y, i + 1))
    S.append(text(SX + 48, y + 4, t, 12, INTER, 600, STAR))
    S.append(text(SX + 48, y + 19, d, 11, INTER, 400, MUTED))

S.append(sidebox(SX, 682, SW_, 140, "Failure is a first-class path"))
for i, line in enumerate(
    [
        "ERP 503 → backoff retry, ≤5 attempts → terminal dead",
        "stuck event → durable dead_events, never a silent expiry",
        "a sweep re-drives transient failures every 15 minutes",
        "replay after a crash: one ERP write — proven live",
    ]
):
    S.append(text(SX + 20, 728 + i * 19, line, 11.5, INTER, 400, STAR))

S.append(sidebox(SX, 834, SW_, 132, "Legend"))
ly = 880
S.append(edge([(SX + 24, ly), (SX + 64, ly)], AMBER, 2.4, marker="amber"))
S.append(text(SX + 76, ly + 4, "the case's path — events and decisions", 11, INTER, 400, MUTED))
ly += 18
S.append(edge([(SX + 24, ly), (SX + 64, ly)], MUTED, 1.6))
S.append(text(SX + 76, ly + 4, "data read / write", 11, INTER, 400, MUTED))
ly += 18
S.append(edge([(SX + 24, ly), (SX + 64, ly)], STAR, 1.6, dash="6 4", marker="star"))
S.append(text(SX + 76, ly + 4, "private PSC-I connection — no public path", 11, INTER, 400, MUTED))
ly += 18
S.append(rrect(SX + 24, ly - 7, 40, 14, r=4, fill="rgba(251,191,36,0.05)", stroke=AMBER_BRIGHT, sw=1.2, dash="4 3"))
S.append(text(SX + 76, ly + 4, "model-exposure boundary (spark = calls Gemini)", 11, INTER, 400, MUTED))
ly += 18
S.append(badge(SX + 44, ly, 5))
S.append(text(SX + 76, ly + 4, "enforcement point — see the numbered list", 11, INTER, 400, MUTED))

S.append(sidebox(SX, 978, SW_, 86, "In plain terms"))
for i, line in enumerate(
    [
        "Frontend: Ground Control (console + review)",
        "Backend: Cloud Run adapters + Agent Runtime graph",
        "Databases: Firestore · Agent Sessions · ERPNext",
    ]
):
    S.append(text(SX + 20, 1020 + i * 16, line, 11.5, INTER, 400, STAR))

S.append("</svg>")
OUT.write_text("\n".join(S))
print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB)")
