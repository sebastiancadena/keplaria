#!/usr/bin/env python3
"""Generate the keplaria.com static site into site/dist/.

    uv run python site/build_site.py

Two pages: a front door, and the verification ledger the front door links to.

WHY THIS IS GENERATED. The ledger page states every public number this project
makes, and a hand-written copy of those numbers is exactly the artefact that
goes stale without anyone noticing -- this repo has been bitten by that twice.
Here the numbers are read from `docs/proof/claims.toml` and re-rendered from
the evidence files at build time, so the page cannot disagree with the ledger:
there is no second copy to drift. `scripts/doctor.sh` byte-compares the built
pages against this script's output, so an edit without a rebuild fails.

Fonts are loaded from Google Fonts rather than self-hosted: the site is one
static page with no build pipeline, and the three brand faces are all
available there. If that ever becomes unacceptable, self-host from
`console/static/fonts/` and drop the <link>.
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.claim_ledger import load, resolve  # noqa: E402

OUT = Path(os.environ.get("KEPLARIA_SITE_OUT", Path(__file__).parent / "dist"))
LEDGER = ROOT / "docs" / "proof" / "claims.toml"
LOCKUP = ROOT / "docs" / "architecture" / "assets" / "keplaria-lockup-horizontal-dark.svg"
# The brand repo ships a full social kit that nothing referenced until now.
ASSETS_REPO = Path.home() / "dev" / "git" / "keplaria-assets"
OG_IMAGE = ASSETS_REPO / "assets" / "social" / "og-image.png"

CONSOLE = "https://keplaria-console-bklu5jcdea-uc.a.run.app"
REPO = "https://github.com/sebastiancadena/keplaria"

CSS = """
:root{--void:#0B1020;--ink:#111827;--amber:#F59E0B;--amber-bright:#FBBF24;
--star:#F8FAFC;--muted:#64748B;--clear:#34D399;
--border:rgb(100 116 139 / .32);--hair:rgb(100 116 139 / .16)}
*{box-sizing:border-box}
body{margin:0;background:var(--void);color:var(--star);
font-family:Inter,system-ui,sans-serif;line-height:1.65;
-webkit-font-smoothing:antialiased}
.wrap{max-width:64rem;margin:0 auto;padding:3rem 1.5rem 5rem}
header{display:flex;align-items:center;justify-content:space-between;
gap:1rem;flex-wrap:wrap;margin-bottom:4rem}
header svg{height:96px;width:auto}
/* The lockup viewBox carries 0.5x-icon clear space, so the artwork is
   74.74% of the element width and HALF its height. 96px tall renders a
   ~142px-wide mark, clear of the 96px lockup minimum; 46px did not. */
nav a{color:var(--muted);text-decoration:none;margin-left:1.5rem;font-size:.95rem}
nav a:hover{color:var(--star)}
h1{font-family:"Space Grotesk",system-ui,sans-serif;font-weight:600;
font-size:clamp(2rem,5vw,3.1rem);line-height:1.15;margin:0 0 1.25rem;
letter-spacing:-.02em}
h2{font-family:"Space Grotesk",system-ui,sans-serif;font-weight:600;
font-size:1.4rem;margin:3.5rem 0 1rem}
.lede{font-size:1.2rem;max-width:44ch;color:var(--star)}
.sub{color:var(--muted);max-width:62ch}
.amber{color:var(--amber-bright)}
.figures{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
margin:3rem 0}
.fig{border:1px solid var(--border);border-radius:14px;padding:1.25rem;
background:rgb(248 250 252 / .03)}
.fig b{display:block;font-family:"Space Grotesk",system-ui,sans-serif;
font-size:1.7rem;font-weight:600;color:var(--amber-bright);margin-bottom:.35rem}
.fig span{color:var(--muted);font-size:.92rem}
.actions{display:flex;gap:.75rem;flex-wrap:wrap;margin:2.5rem 0}
.btn{display:inline-block;padding:.7rem 1.15rem;border-radius:10px;
border:1px solid var(--border);color:var(--star);text-decoration:none;
font-size:.97rem}
.btn--go{border-color:var(--amber);color:var(--amber-bright)}
.btn:hover{background:rgb(248 250 252 / .05)}
table{width:100%;border-collapse:collapse;margin-top:1.5rem;font-size:.93rem}
th{text-align:left;font-weight:600;color:var(--muted);font-size:.78rem;
letter-spacing:.07em;text-transform:uppercase;padding:.6rem .5rem;
border-bottom:1px solid var(--border)}
td{padding:.7rem .5rem;border-bottom:1px solid var(--hair);vertical-align:top}
.val{font-family:"JetBrains Mono",ui-monospace,monospace;color:var(--amber-bright);
white-space:nowrap}
.q{color:var(--muted);font-size:.86rem}
code,.mono{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.86em}
.note{border-left:3px solid var(--amber);padding:.85rem 1.15rem;
background:rgb(248 250 252 / .03);border-radius:0 12px 12px 0;margin:2rem 0;
color:var(--star)}
footer{margin-top:5rem;padding-top:2rem;border-top:1px solid var(--hair);
color:var(--muted);font-size:.88rem}
a{color:var(--amber-bright)}
.scroll{overflow-x:auto}
"""


def shell(title: str, body: str, desc: str, canonical: str) -> str:
    lockup = LOCKUP.read_text().strip()
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<meta name="color-scheme" content="dark">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Keplaria">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:image" content="https://keplaria.com/og-image.png">
<meta property="og:url" content="{canonical}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://keplaria.com/og-image.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght@400&family=Space+Grotesk:wght@500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body><div class="wrap">
<header><a href="/" aria-label="Keplaria">{lockup}</a>
<nav><a href="/proof">Verification</a><a href="{REPO}">Source</a></nav></header>
{body}
<footer>
<p>Keplaria is a hackathon project. The screening index is a <b>synthetic,
rights-cleared watchlist</b> &mdash; no live sanctions data is indexed. The ERP is a
real ERPNext instance holding fictional suppliers. The manual baseline is
<b>author-timed, not practitioner-reviewed</b>.</p>
<p>&copy; 2026 &middot; <a href="{REPO}">source</a> &middot; <a href="/proof">every number, with its evidence</a></p>
</footer></div></body></html>
"""


def figures(claims) -> str:
    """Three numbers, each read from the ledger rather than typed here."""
    want = ("run_machine_seconds", "manual_baseline_seconds",
            "policy_required_interventions")
    labels = {
        "run_machine_seconds": "of machine work, one deployed run",
        "manual_baseline_seconds": "for the same work by hand, timed",
        "policy_required_interventions": "decision a human had to make",
    }
    out = []
    for claim in claims:
        if claim.id not in want:
            continue
        value = resolve(claim, ROOT).split(" ")[0]
        unit = "s" if "seconds" in claim.id else ""
        out.append(f'<div class="fig"><b>{html.escape(value)}{unit}</b>'
                   f'<span>{labels[claim.id]}</span></div>')
    return f'<div class="figures">{"".join(out)}</div>'


def index(claims) -> str:
    return shell(
        "Keplaria",
        f"""
<h1>Supplier onboarding usually ends<br>when the ERP record is created.</h1>
<p class="lede">Keplaria keeps governing that supplier for months afterwards.</p>
<p class="sub">A fleet of agents onboards a supplier, screens it for sanctions,
and then stays with it &mdash; requesting certificate renewals, applying a
purchasing hold when evidence goes overdue, and releasing that hold when valid
new evidence arrives. An LLM coordinator <em>proposes</em> which specialists
should run; a deterministic policy layer <em>decides</em>, against a versioned
catalog. Nothing reaches the ERP except through an outbox, and a human can stop
any case on the way.</p>
{figures(claims)}
<div class="note"><b>Every number on this site is bound to the run that
produced it.</b> The verification page is generated from the evidence files, not
written by hand &mdash; so it cannot quietly disagree with them.</div>
<div class="actions">
<a class="btn btn--go" href="{CONSOLE}">Open the live case console</a>
<a class="btn" href="/proof">Verification ledger</a>
<a class="btn" href="{REPO}">Source</a>
</div>
<h2>Named for the law, not the planets</h2>
<p class="sub">An agent that runs for minutes can afford to improvise. One that
stays accountable for months cannot &mdash; which is why the model only
<em>proposes</em> here, and a versioned, deterministic policy <em>decides</em>.
The name marks that line. Kepler&rsquo;s breakthrough was not noticing that
planets move; everyone could see that. It was showing that their motion obeys
law: predictable, calculable, correctable. That is what lets you launch a case
once and have it stay up without constant thrust &mdash; when compliance decays,
and a certificate nears expiry, policy fires a small correction: a renewal
request, a purchasing hold, a hold release. Spacecraft engineers call those
corrections station-keeping. So do we.</p>
<h2>What a case looks like</h2>
<p class="sub">The console is public and read-only. A case shows what the
coordinator proposed, what policy actually engaged, the screening candidates and
why one of them needed a person, and every command &mdash; including the ones
policy refused. A case stopped for a human says so plainly, and says that
nothing has been written.</p>
<h2>Built on</h2>
<p class="sub mono">Agent Development Kit &middot; Gemini &middot; Agent Runtime
&middot; Cloud Run &middot; Firestore &middot; Pub/Sub &middot; ERPNext &middot;
OpenTelemetry</p>
""",
        "Supplier onboarding that does not end when the ERP record is created.",
        "https://keplaria.com/",
    )


def proof(claims) -> str:
    rows = []
    for claim in claims:
        result = resolve(claim, ROOT) if claim.verify == "evidence" else None
        value = (f'<span class="val">{html.escape(result)}</span>' if result
                 else '<span class="q">read the evidence file</span>')
        qualifier = (f'<div class="q">{html.escape(claim.qualifier)}</div>'
                     if claim.qualifier else "")
        rows.append(
            f"<tr><td>{html.escape(claim.claim)}{qualifier}</td>"
            f"<td>{value}</td>"
            f'<td class="mono">{html.escape(claim.evidence or "—")}</td></tr>'
        )
    return shell(
        "Verification — Keplaria",
        f"""
<h1>Every number, and the run that produced it.</h1>
<p class="sub">This page is generated from <code>docs/proof/claims.toml</code>
and the evidence files it points at. It is not written by hand. The same
generator runs in the project's health check, so a number that drifts from its
evidence fails the build rather than reaching a reader.</p>
<div class="note">Two claims are deliberately unpublished and say so in the
ledger, and several are marked <b>read by a human</b>: they are historical or
rounded statements that no automatic check can settle, and pretending otherwise
would be a false green.</div>
<div class="scroll">
<table><thead><tr><th>Claim</th><th>Value</th><th>Evidence</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
""",
        "Every public number Keplaria states, bound to the run that produced it.",
        "https://keplaria.com/proof",
    )


def main() -> int:
    claims = load(LEDGER)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(index(claims))
    (OUT / "proof.html").write_text(proof(claims))
    if OG_IMAGE.exists():
        (OUT / "og-image.png").write_bytes(OG_IMAGE.read_bytes())
    else:
        print(f"WARN: {OG_IMAGE} missing — the share card will 404",
              file=sys.stderr)
    print(f"wrote {OUT}/index.html and {OUT}/proof.html ({len(claims)} claims)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
