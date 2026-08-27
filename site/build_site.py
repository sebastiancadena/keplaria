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
ORIENTATION = ROOT / "docs" / "architecture" / "orientation.svg"
# The brand repo ships a full social kit that nothing referenced until now.
ASSETS_REPO = Path.home() / "dev" / "git" / "keplaria-assets"
OG_IMAGE = ASSETS_REPO / "assets" / "social" / "og-image.png"

CONSOLE = "https://keplaria-console-bklu5jcdea-uc.a.run.app"
REVIEW = "https://keplaria-review-bklu5jcdea-uc.a.run.app/review"
REPO = "https://github.com/sebastiancadena/keplaria"

# One line above each group on /proof, saying in plain language what the
# group proves -- not what the numbers are (the table already says that).
# Assertion in proof() keeps this list honest if claims.toml grows a claim
# nobody assigned to a group.
GROUPS = (
    ("The run's own clock, compared against the same work timed by hand.",
     ("run_machine_seconds", "run_human_seconds", "run_parked_seconds", "run_budget_seconds",
      "manual_baseline_seconds", "manual_baseline_steps",
      "manual_steps_eliminated", "simulated_business_days")),
    ("Checks this run re-executes every time it reports them, not numbers "
     "quoted from an earlier pass.",
     ("core_contracts_count", "domain_eval_cases", "domain_eval_mean_score",
      "contract_suite_passed")),
    ("What that run actually wrote to the ERP (enterprise resource planning: "
     "the system of record for suppliers and purchasing), and what "
     "it refused to write twice.",
     ("fields_without_rekeying", "enforced_hold_days",
      "policy_required_interventions", "duplicate_writes_after_retry")),
    ("What running this project costs to operate, kept here for "
     "traceability even where it hasn&rsquo;t been turned into a prose "
     "claim anywhere else yet.",
     ("gross_cost_month_to_date", "credit_remaining",
      "yente_uptime_hours_per_day")),
    ("A second Google model, measured against this project&rsquo;s own "
     "documents. Reading them correctly was not enough to adopt it, so both "
     "halves of that result are published together.",
     ("gemma_content_correct", "gemma_format_conformant",
      "gemma_local_format_conformant", "gemma_served_median_seconds")),
    ("Statements a human read and confirmed by hand, because no automatic "
     "check can settle them.",
     ("hold_before_decision", "eval_suite_at_the_time_of_the_pin",
      "reasoning_tokens_and_timing_at_the_pin")),
    ("What the published build-piece article already said, checked here "
     "against the run it described at the time.",
     ("article_eval_suite_at_publication", "article_run_timing_at_publication")),
)

CSS = """
:root{--void:#0B1020;--ink:#111827;--amber:#F59E0B;--amber-bright:#FBBF24;
--star:#F8FAFC;--muted:#64748B;--clear:#34D399;
--border:rgb(100 116 139 / .32);--hair:rgb(100 116 139 / .16);
--axis:rgb(100 116 139 / .38);
--rail:11rem;--gutter:3rem;--measure:100%;--indent:0rem}
*{box-sizing:border-box}
body{margin:0;background:var(--void);color:var(--star);
font-family:Inter,system-ui,sans-serif;line-height:1.65;
-webkit-font-smoothing:antialiased}
:focus-visible{outline:2px solid var(--amber-bright);outline-offset:3px}

/* ONE LEFT EDGE, AND ONLY ONE.
   Every block starts on the same vertical axis; the only thing that varies is
   how far RIGHT it runs -- one measure for prose, the full span for evidence.
   Before this, blocks were centred at seven different max-widths (56rem, 52ch,
   62ch, 41rem, two full-width flex rows and the table), which put a ragged edge
   on BOTH sides of every one of them. Narrow viewports collapsed them all to
   the same width and hid it; a wide display turned the page into an accordion.
   Section headings hang in the margin LEFT of the axis. Nothing else crosses
   it -- not the header, not the footer, not a card grid. If you add a block,
   it goes in a .secbody and inherits the axis; do not give it its own
   max-width and margin:auto. */
.wrap{max-width:38rem;margin:0 auto;padding:3rem 1.5rem 5rem}
header{display:flex;align-items:center;justify-content:space-between;
gap:1rem;flex-wrap:wrap;margin:0 0 4.5rem var(--indent)}
header svg{height:96px;width:auto}
/* The lockup viewBox carries 0.5x-icon clear space, so the artwork is
   74.74% of the element width and HALF its height. 96px tall renders a
   ~142px-wide mark, clear of the 96px lockup minimum; 46px did not. */
nav a{color:var(--muted);text-decoration:none;margin-left:1.5rem;font-size:.95rem}
nav a:hover{color:var(--star)}

/* A section is a heading plus its body. The heading moves into the margin at
   the widest stage; below that it simply sits above the body. Each section is
   its own grid so the heading top-aligns with its body without the two of them
   negotiating margins across a shared row. */
.axis{position:relative}
.sec{display:grid;grid-template-columns:minmax(0,1fr)}
.secbody>*{max-width:var(--measure)}
.secbody>h1,.secbody>.figures,.secbody>.evalgrid,.secbody>.actions,
.secbody>.scroll,.secbody>.figure{max-width:none}

h1{font-family:"Space Grotesk",system-ui,sans-serif;font-weight:600;
font-size:clamp(2rem,4.4vw,3.25rem);line-height:1.12;margin:0 0 1.5rem;
letter-spacing:-.02em;position:relative}
h2{font-family:"Space Grotesk",system-ui,sans-serif;font-weight:600;
font-size:1.4rem;margin:3.5rem 0 1rem;letter-spacing:-.01em}
.lede{font-size:1.22rem;margin:0 0 1.5rem;color:var(--star)}
.sub{color:var(--muted);margin:0 0 1.25rem}
.amber{color:var(--amber-bright)}

/* Cards were boxes: a border on four sides broadcasts a block's width, which
   is the thing this page is trying to stop doing. A single rule along the top
   states the column and leaves the left edge to the axis. */
.figures{display:grid;gap:2rem 2.5rem;
grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));margin:2.75rem 0}
.fig{border-top:1px solid var(--border);padding:1rem 0 0}
.fig b{display:block;font-family:"Space Grotesk",system-ui,sans-serif;
font-size:2rem;font-weight:600;color:var(--amber-bright);line-height:1.1;
margin-bottom:.4rem}
.fig span{display:block;color:var(--muted);font-size:.9rem;line-height:1.5;
max-width:17rem}
.figure{display:block;width:100%;height:auto;margin:1.25rem 0;
border:1px solid rgba(100,116,139,.32);border-radius:12px}
.evalgrid{display:grid;gap:2rem 2.5rem;
grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));margin:.75rem 0 0}
.evalstep{border-top:1px solid var(--border);padding:1rem 0 0}
.evalstep a{text-decoration:none}
.evalstep b{font-family:"Space Grotesk",system-ui,sans-serif;font-weight:600;
font-size:1.125rem;color:var(--amber-bright)}
.evalstep a:hover b{text-decoration:underline}
.evalstep p{color:var(--muted);font-size:.9rem;margin:.6rem 0 0}
.actions{display:flex;gap:.75rem;flex-wrap:wrap;margin:2.5rem 0 0}
.aside{color:var(--muted);font-size:.88rem;margin:.9rem 0 0}
.btn{display:inline-block;padding:.7rem 1.15rem;border-radius:10px;
border:1px solid var(--border);color:var(--star);text-decoration:none;
font-size:.97rem}
.btn--go{border-color:var(--amber);color:var(--amber-bright)}
.btn:hover{background:rgb(248 250 252 / .05)}
table{width:100%;border-collapse:collapse;margin-top:1.5rem;font-size:.93rem;
table-layout:fixed;min-width:32rem}
th:nth-child(1){width:42%}th:nth-child(2){width:22%}
th:nth-child(3){width:36%}
td:nth-child(2),td:nth-child(3){overflow-wrap:anywhere}
th{text-align:left;font-weight:600;color:var(--muted);font-size:.78rem;
letter-spacing:.07em;text-transform:uppercase;padding:.6rem .5rem .6rem 0;
border-bottom:1px solid var(--border)}
td{padding:.7rem .5rem .7rem 0;border-bottom:1px solid var(--hair);
vertical-align:top}
.grouplead td{padding:1.6rem 0 .5rem;color:var(--star);font-weight:600;
font-size:.95rem;border-bottom:1px solid var(--border)}
.val{font-family:"JetBrains Mono",ui-monospace,monospace;
color:var(--amber-bright)}
.q{color:var(--muted);font-size:.86rem}
code,.mono{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.86em}
.note{border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);
padding:1.1rem 0;margin:2.25rem 0;color:var(--star)}
footer{margin:5rem 0 0 var(--indent);padding-top:2rem;
border-top:1px solid var(--hair);color:var(--muted);font-size:.88rem}
a{color:var(--amber-bright)}
.scroll{overflow-x:auto}

/* Stage 2: still one column, but prose holds its measure while evidence runs
   the full width. Same left edge for both. */
@media (min-width:60rem){
.wrap{max-width:56rem;padding:3.5rem 2rem 6rem}
:root{--measure:34rem}
}

/* Stage 3: the heading moves into the margin and the axis is drawn. */
@media (min-width:78rem){
:root{--indent:calc(var(--rail) + var(--gutter))}
.wrap{max-width:72rem;padding:4rem 2rem 7rem}
.sec{grid-template-columns:var(--rail) minmax(0,var(--measure)) minmax(0,1fr);
column-gap:var(--gutter)}
.sec>h2{grid-column:1;align-self:start;text-align:right;position:relative;
margin:0;padding-top:.2rem;font-size:1.125rem;line-height:1.4;
text-wrap:balance;
letter-spacing:0;color:var(--star)}
.sec>.secbody{grid-column:2/-1}
.sec+.sec{margin-top:4.5rem}
/* The axis, and the corrections applied along it. The disc punches a gap in
   the hairline exactly as the mark's orbital ring breaks around the body
   crossing it (brand guidelines, BODY_GAP). */
.axis::before{content:"";position:absolute;
left:calc(var(--rail) + var(--gutter)/2);top:.6rem;bottom:0;width:1px;
background:linear-gradient(var(--axis) calc(100% - 7rem),transparent);
transform-origin:top;
animation:axis-draw .9s cubic-bezier(.2,.7,.3,1) both}
.sec>h2::after,.sec--hero h1::before{content:"";position:absolute;
width:7px;height:7px;border-radius:50%;background:var(--amber);
box-shadow:0 0 0 6px var(--void)}
.sec>h2::after{top:.62rem;right:calc(var(--gutter)/-2 - 3.5px)}
.sec--hero h1::before{top:.44em;left:calc(var(--gutter)/-2 - 3.5px)}
}
@keyframes axis-draw{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@media (prefers-reduced-motion:reduce){.axis::before{animation:none}}
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
<main class="axis">{body}</main>
<footer>
<p>Keplaria is a hackathon project. The screening index is a <b>synthetic,
rights-cleared watchlist</b>; no live sanctions data is indexed. The ERP is a
real ERPNext instance holding fictional suppliers. The manual baseline is
<b>author-timed, not practitioner-reviewed</b>.</p>
<p>&copy; 2026 &middot; <a href="{REPO}">source</a> &middot; <a href="/proof">every number, with its evidence</a></p>
</footer></div></body></html>
"""


def figures(claims) -> str:
    """Four numbers, each read from the ledger rather than typed here — the
    step total included, so the "19 of 20" label cannot drift from the
    baseline claim it summarises."""
    by_id = {claim.id: claim for claim in claims}

    def value(claim_id: str) -> str:
        return resolve(by_id[claim_id], ROOT).split(" ")[0]

    figs = (
        (value("run_machine_seconds") + "s",
         "of machine work, one deployed run of the whole lifecycle"),
        (value("manual_baseline_seconds") + "s",
         "for the same work by hand, timed "
         "(author-timed, not practitioner-reviewed)"),
        (value("manual_steps_eliminated"),
         f'of {value("manual_baseline_steps")} manual steps removed; the last '
         "is the approval policy requires"),
        (value("simulated_business_days"),
         "simulated business days of renewal, hold and release in one recording"),
    )
    out = [f'<div class="fig"><b>{html.escape(v)}</b><span>{label}</span></div>'
           for v, label in figs]
    return f'<div class="figures">{"".join(out)}</div>'


def index(claims) -> str:
    return shell(
        "Keplaria",
        f"""
<section class="sec sec--hero"><div class="secbody">
<h1>Supplier compliance doesn&rsquo;t end at onboarding. Most tools do.</h1>
<p class="lede">Certificates expire months after a supplier is onboarded, and
every onboarding tool retires the day the ERP record is created (enterprise
resource planning: the business&rsquo;s system of record for suppliers and
purchasing). The ongoing work of noticing the expiry, chasing the renewal,
and deciding whether it is still safe to buy goes back to a person with a
calendar reminder.</p>
<p class="sub">Keplaria stays. One durable case per supplier wakes months
later on its own clock: it requests renewed evidence, places a reversible
purchasing hold when a certificate lapses, checks the renewal against the
source document, and releases the hold. A language-model coordinator
<em>proposes</em> which specialist agents should run; a deterministic policy
layer <em>decides</em>, against a versioned catalog.</p>
{figures(claims)}
<p class="sub">It stops exactly where policy requires a human decision
&mdash; and nowhere else. Nothing reaches the ERP except through an outbox (a
queue of pending ERP writes, released only on approval).</p>
<div class="note"><b>Every number on this site is bound to the run that
produced it.</b> The verification page is generated from the evidence files,
not written by hand, so it cannot quietly disagree with them.</div>
<div class="actions">
<a class="btn btn--go" href="{CONSOLE}">Open the case console</a>
<a class="btn" href="/proof">Verification ledger</a>
<a class="btn" href="{REPO}">Source</a>
</div>
<p class="aside">The case console is a real deployment. The suppliers in it are
synthetic demo data.</p>
</div></section>
<section class="sec"><h2>Evaluate this in three minutes</h2>
<div class="secbody"><div class="evalgrid">
<div class="evalstep"><a href="{CONSOLE}"><b>1&nbsp;&middot; Case console</b></a>
<p>No sign-in. Cases are grouped by supplier; each row is one payload, one
supplier&rsquo;s case. Open one: a context strip and a lifecycle indicator
(onboarded &rarr; active &rarr; renewal requested &rarr; held &rarr; released)
show where it stands, and the status line says exactly what has been written
to the ERP so far. For a parked case, stopped for a human decision at any
stage, that is nothing yet.</p>
</div>
<div class="evalstep"><a href="{REVIEW}"><b>2&nbsp;&middot; Review console
(Ground Control)</b></a>
<p>Google sign-in, enforced by Cloud IAP, Google&rsquo;s identity check in
front of the service. Cases the policy stopped wait here with their ERP
writes held; approving is what releases them.</p></div>
<div class="evalstep"><b>3&nbsp;&middot; Demonstration video</b>
<p><a href="https://youtu.be/54GiU75AjH4">https://youtu.be/54GiU75AjH4</a> (3:23). What it shows: one continuous, unedited take. A stop for
a human, an approval that releases the held writes, then a simulated year
and a half of renewals, a hold, and a release.</p></div>
</div></div></section>
<section class="sec"><h2>Named for the law, not the planets</h2>
<div class="secbody"><p class="sub">An agent that runs for minutes can afford to improvise. One that
stays accountable for months cannot. That is why the model only
<em>proposes</em> here, and a versioned, deterministic policy <em>decides</em>.
The name marks that line. Kepler&rsquo;s breakthrough was not noticing that
planets move; everyone could see that. It was showing that their motion obeys
law: predictable, calculable, correctable. That is what lets you launch a case
once and have it stay up without constant thrust. When compliance decays and a
certificate nears expiry, policy fires a small correction: a renewal
request, a purchasing hold, a hold release. Spacecraft engineers call those
corrections station-keeping. So do we.</p></div></section>
<section class="sec"><h2>The fleet and the payload</h2>
<div class="secbody"><p class="sub"><strong>The fleet is the crew and its
rulebook:</strong> three departments, a coordinator that proposes, two
specialist agents, and the five ERP commands they may issue. <strong>A
payload is one supplier&rsquo;s case</strong>, carried through that fleet for
months: onboarded, then renewals, a hold, a release.</p>
<img class="figure" src="/orientation.svg" width="1200" height="520"
     alt="The fleet: departments, agents and commands. One payload: a supplier's case carried through the fleet across five lifecycle stops.">
<p class="sub">The console is public and read-only. A case shows what the
coordinator proposed, what policy actually engaged, the candidates from
screening against a sanctions watchlist and why one of them needed a person,
and every command, including the ones policy refused. A case stopped
for a human says so plainly, and says that nothing has been written. The
<a href="{CONSOLE}/fleet">fleet page</a> is the rulebook, with a count of
how many cases exercised each rule.</p></div></section>
<section class="sec"><h2>The security model, in six claims</h2>
<div class="secbody"><div class="evalgrid">
<div class="evalstep"><b>No agent holds a credential</b>
<p>No agent holds a credential or a write tool, and a clock event never
reaches a language-model agent at all.</p></div>
<div class="evalstep"><b>The executor is scoped</b>
<p>The one ERP credential belongs to the deterministic executor, under a role
that cannot delete, cannot widen its own permissions, and cannot read a
financial document. Those limits are read back off the live ERP by the health
check, not assumed.</p></div>
<div class="evalstep"><b>Tainted documents stay outside</b>
<p>A document flagged as tainted is never shown to any agent, and its case is
forced to a blocked verdict, so a tainted document cannot cause an ERP
write.</p></div>
<div class="evalstep"><b>Identity is verified, not asserted</b>
<p>The reviewer&rsquo;s identity comes from a signed IAP assertion, never from
a header a caller could set.</p></div>
<div class="evalstep"><b>The engine has no internet path</b>
<p>The reasoning engine has no route to the public internet: it reaches the
screening service over Private Service Connect only, and secrets come from
Secret Manager, per service identity.</p></div>
<div class="evalstep"><b>Failure becomes durable state</b>
<p>Never a silent exception: a failed ERP command stays durable and an
unattended sweep re-drives it to exactly one record.</p></div>
</div>
<p class="sub">Each claim is enforced by a test or a deployed check, itemised
in <a href="{REPO}#security-model-in-six-claims">the README&rsquo;s security
section</a>. Two limits, stated rather than hidden: a parked case is durable
state, not a suspended run; and a supplier who becomes sanctioned after
onboarding cannot yet be held.</p></div></section>
<section class="sec"><h2>Built on</h2>
<div class="secbody"><p class="sub mono">Agent Development Kit &middot; Gemini &middot; Agent Runtime
&middot; Cloud Run &middot; Firestore &middot; Pub/Sub &middot; ERPNext &middot;
OpenTelemetry</p>
""",
        "Supplier onboarding that does not end when the ERP record is created.",
        "https://keplaria.com/",
    )


def _proof_row(claim) -> str:
    # A hold recorded in the ledger must reach this page: rendering the value
    # anyway would publish the very number the reason says not to.
    held = (claim.verify == "evidence" and bool(claim.reason)
            and not claim.appears_in)
    result = (resolve(claim, ROOT)
              if claim.verify == "evidence" and not held else None)
    value = (f'<span class="val">{html.escape(result)}</span>' if result
             else '<span class="q">held (reason in the ledger)</span>' if held
             else '<span class="q">read the evidence file</span>')
    qualifier = (f'<div class="q">{html.escape(claim.qualifier)}</div>'
                 if claim.qualifier else "")
    # The evidence path is a live link into the public repository: a page
    # whose pitch is "every number, with its evidence" must not make the
    # reader reconstruct the URL by hand (flagged by the logged-out review).
    evidence = (
        f'<a class="mono" href="{REPO}/blob/main/{html.escape(claim.evidence)}">'
        f"{html.escape(claim.evidence)}</a>"
        if claim.evidence else '<span class="mono">&mdash;</span>'
    )
    return (
        f"<tr><td>{html.escape(claim.claim)}{qualifier}</td>"
        f"<td>{value}</td>"
        f"<td>{evidence}</td></tr>"
    )


def proof(claims) -> str:
    by_id = {claim.id: claim for claim in claims}
    covered = {claim_id for _, ids in GROUPS for claim_id in ids}
    missing = {claim.id for claim in claims} - covered
    if missing:
        raise ValueError(f"claims.toml has ungrouped claims: {sorted(missing)}")
    rows = []
    for lead, ids in GROUPS:
        rows.append(f'<tr class="grouplead"><td colspan="3">{lead}</td></tr>')
        rows.extend(_proof_row(by_id[claim_id]) for claim_id in ids)
    return shell(
        "Verification — Keplaria",
        f"""
<section class="sec sec--hero"><div class="secbody">
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
</div></section>
""",
        "Every public number Keplaria states, bound to the run that produced it.",
        "https://keplaria.com/proof",
    )


def main() -> int:
    claims = load(LEDGER)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(index(claims))
    (OUT / "proof.html").write_text(proof(claims))
    (OUT / "orientation.svg").write_bytes(ORIENTATION.read_bytes())
    if OG_IMAGE.exists():
        (OUT / "og-image.png").write_bytes(OG_IMAGE.read_bytes())
    else:
        print(f"WARN: {OG_IMAGE} missing — the share card will 404",
              file=sys.stderr)
    print(f"wrote {OUT}/index.html and {OUT}/proof.html ({len(claims)} claims)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
