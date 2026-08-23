# Devpost submission copy

Paste-ready text for the Devpost submission form, one section per field. It
lives here rather than only in the form so that every number in it is bound to
the run that produced it: `docs/proof/claims.toml` cites this file, and
`uv run python scripts/claim_ledger.py --check` re-reads each evidence file and
fails if the prose and the evidence disagree. **Edit the number here and the
check will catch you; edit the evidence and it will catch that too.**

The required content is set by the [Official
Rules](https://allthingsagentichackathon.devpost.com/rules): the text
description must carry the project's features and functionality, the
technologies used, any other data sources, and the findings and learnings. The
four sections below are those four, in that order.

Two things are deliberately absent and must not be invented before they exist:
the demonstration video URL and the frozen commit the submission cites.

---

## Tagline

> Suppliers are onboarded in seconds — then governed for months. Keplaria
> wakes to chase expiring certificates, holds purchasing when evidence
> lapses, and releases the hold when the renewal checks out. The model
> proposes; deterministic policy decides. Work in orbit, not work finished.

## Category

**The Fortified Enterprise Fleet.**

## Links

| Field | Value |
|---|---|
| Project home | <https://keplaria.com> — front door, and <https://keplaria.com/proof> for every published number bound to the run that produced it |
| Try it out — case console | <https://keplaria-console-bklu5jcdea-uc.a.run.app> (no sign-in) |
| Try it out — review console | <https://keplaria-review-bklu5jcdea-uc.a.run.app/review> (Google sign-in through Cloud IAP) |
| Code repository | <https://github.com/sebastiancadena/keplaria> |
| Demonstration video | TODO — record day 17, add before submitting |
| Architecture diagram | `docs/architecture/architecture.png`, generated from committed sources by `docs/architecture/build.py` |

---

## What it does

Supplier compliance is not a one-time check: certificates expire months after
onboarding, and someone has to notice. A beverage manufacturer needs a new
packaging supplier approved. Keplaria runs
the onboarding — document intake, field extraction, screening against a
synthetic, rights-cleared watchlist — and then does the part nobody builds: it
stays. Approval was never the hard part. The hard question is whether an agent
can remain accountable for an obligation across time. So the case does not close
when the ERP record is created. For months afterwards it keeps governing that
supplier — requesting certificate renewals, applying a purchasing hold when
evidence goes overdue, releasing it when a valid renewal arrives. An LLM
coordinator proposes which specialist agents run; a deterministic policy layer
decides, against a versioned catalog. Nothing reaches the ERP except through an
outbox, and on a sanctions near-match the system stops — it does not
adjudicate.

Keplaria is a durable agent workflow — a **mission** — that does that work and
keeps doing it. One mission is created when the supplier is onboarded, and the
same mission wakes on its own clock months later. It requests renewed evidence,
places a reversible purchasing hold when the certificate goes overdue, reads the
renewal document that eventually arrives, checks the values it extracted against
the document they came from, screens the supplier against a watchlist, scores it
against a versioned policy, and releases the hold. Nothing about that sequence
is a chatbot: the system decides, and it writes those decisions into a real ERP.

It stops for a human exactly where policy says a human must decide. A supplier
that scores into the review band parks, and the ERP writes it would have made
are queued rather than executed until a named reviewer approves them in
**Ground Control**, the human decision console. Approval is what releases the
write. A rejection is equally binding, and a protective hold is applied whether
or not anyone ever opens the page.

A compliance relationship has two sides, and the other one is not a corporate
role at all. The party who must keep a certificate current is often a small
supplier — a family packaging firm with no compliance department and no room
for one more portal. Keplaria deliberately asks nothing of them: no portal, no
login, no account, no training. The renewal request arrives as a real email
sent by the deployed workflow, and answering it is everything the design asks
of them.
One boundary is stated rather than implied: in this prototype the returned
certificate enters the system as a published event — ingesting the reply
itself from a mailbox is design intent, not built, and the video labels it
that way on screen.

One deployed run of that whole loop, captured in
[`spikes/judge_run/evidence.json`](../../spikes/judge_run/evidence.json)
(in this repository a *spike* is a self-contained proof directory: the
harness that was run and the evidence file it wrote), covers
two suppliers: one that parks for a human, and one that runs the full lifecycle
from onboarding to hold release. It took **55.3 s of machine time** — including
a 43.9 s cold start — against a budget of **130s**, plus a single
**47.7 s human approval**, which is timed separately and excluded from that
total because it is a person's time, not the system's.

The same work done by hand took **663.5 s** across **20 steps**, timed by a
person actually doing it — **19 of which the run removes**. The twentieth is the
approval that policy requires a human to make. That baseline is
**author-timed, not practitioner-reviewed**: no procurement or compliance
practitioner reviewed the step list or the timings, and the number is labelled
that way everywhere it appears, including here. It is a measurement, not a
validation.

What the run measured, beyond the clock:

- **22 fields** were entered into the ERP without a person retyping them.
- **1 policy-required intervention** — one decision, and the system stopped for
  it rather than deciding it.
- **5 enforced hold days**: the window between the supplier going overdue and
  the hold being released. It is claimed only because both the hold and its
  release actually executed in the ERP, which is why it is a statement about
  what the system did rather than about what a hypothetical person failed to
  notice.
- **0 duplicate writes after a retry.** A retried command leaves exactly one ERP
  record, and that is enforced rather than hoped for.
- **380 simulated business days** — about a year and a half — of supplier
  lifecycle. The lifecycle clock is
  compressed so that months of elapsed time fit in a demonstration, and the
  compression is disclosed on screen rather than implied away.

## How it is built

The agent graph is built on the **Google Agent Development Kit (ADK)** for
Python and runs on **Gemini 3.6 Flash**, chosen for speed and determinism after
a blinded comparison against a newer model that was measured and rejected.

The graph is hosted on **Agent Runtime** as a reasoning engine, which
auto-registers it in **Agent Registry** with no publishing step, keeps agent
execution state in **Agent Platform Sessions**, and reaches a private screening
VM over a Private Service Connect interface — that VM has no external IP and is
not reachable from the internet.

Which agents may act on an event is not decided by a model. A versioned
**fleet catalog** — one committed artifact — declares every agent, the complete
route for each event type, and the agents each business department may engage;
it is the same artifact the public console's `/fleet` view renders, so what a
judge sees cataloged is literally what routing enforces. The coordinator model
proposes a route, and deterministic policy corrects that proposal to the
catalog's declared route, recording what it dropped and what it added as an
audit diff on the case. A proposal that reaches across a department boundary —
a finance-labeled event trying to engage the onboarding specialists — is
refused and quarantined rather than corrected, and a catalog that fails to
load refuses every proposal rather than falling back to stale data.

Three **Cloud Run** services cover everything around the graph: an authenticated
**Pub/Sub** push adapter that is the only component able to drive a state change
through the graph, a public read-only case console, and the authenticated review
console behind **Cloud IAP**, where a reviewer's identity is read from a signed
IAP assertion rather than from a header a caller could set.

Authoritative case state lives in **Firestore**, transactionally: the case
version, the event claim that makes duplicate and out-of-order events
impossible, the approval, and the command outbox that makes ERP record writes
idempotent — every record write reconciles against a deterministic external
id. The one stated exception is outbound renewal mail, which the ERP does not
deduplicate, so an accepted send whose response is lost can repeat a message
but never a record. Generative memory is deliberately not trusted with
compliance facts.
Credentials come from **Secret Manager**, each service runs as its own service
account, and the ERP credential is confined rather than spread: only the
deterministic executor holds it — no agent ever does — and the ERP's native
role enforcement is proven with a deliberately unprivileged token that
receives the ERP's own 403 on supplier access. The executor's own key is not
yet role-scoped; the repository flags it for rotation. Traces go to **Cloud Trace** and are
load-bearing rather than decorative: a 10.6 s rise in run time was diagnosed
node by node against them and traced to model reasoning length rather than to
code.

The enterprise systems are real rather than mocked: a hosted **ERPNext** site
holds the supplier records, and a self-hosted **yente** screening service
(backed by Elasticsearch) answers the sanctions-screening calls.

The track names seven platform subsystems, and the repository README says of
each one whether it is used natively, answered by a first-party equivalent, or
deliberately not used — including the one that was measured and turned down. A
subsystem that is not used says so, and says why.

## Data sources

**Every document, supplier, and watchlist entity in this project is synthetic
and was authored for it.** Personal-like fields are fictional and labelled as
such. Nothing here is customer data, production data, or a de-identified
derivative of either.

- **Case documents** — certificates of insurance, food-safety certificates, tax
  and bank-verification letters — are authored fixtures, stored as the redacted
  page-text derivative the pipeline is contracted to produce. That derivative
  is exactly what the Evidence agent receives, and every field it extracts must
  resolve to a verbatim span of it or the case quarantines — real grounded
  extraction, without putting anyone's records into a model prompt. Two limits
  are deliberate and stated in the code as well as here: the PDF/OCR/redaction
  preprocessor that would produce the same derivative from a raster scan is
  future work, and the evidence file attached to the ERP record is a
  well-formed placeholder PDF, not a source scan.
- **The screening index is a synthetic watchlist** authored for this project in
  the FollowTheMoney format, in `fixtures/watchlist/`. The screening service
  fetches nothing from OpenSanctions and indexes no OpenSanctions content. The
  publisher confirmed in writing that bulk download of their data would have
  been permitted for this entry; indexing the synthetic fixture instead is a
  deliberate determinism choice, not a licensing constraint. Their software,
  yente, is used under its MIT licence, and no data-licence claim is made or
  needed.
- **The ERP** is a dedicated demonstration site holding only the synthetic
  suppliers above. Every mutation the system makes is sandboxed to it.

Third-party code, assets, and AI assistance are itemised in the repository's
`THIRD_PARTY.md`.

## Findings and learnings

**The hard problem was not the agents. It was making an agent's work safe to
replay.** Every interesting failure in this build came from the same place: an
event arrives twice, a process dies mid-write, a retry fires after the first
attempt already succeeded. Correct-looking agent code plus an at-least-once
event system produces duplicate suppliers, double holds, and approvals that
apply twice. The fix was to stop treating durability as a property of the agent
and make it a property of the state: a transactional event claim, a case
version, a command ledger whose ids derive from the case and its cycle, and an
approval id derived from the case version it was taken against. **0 duplicate
writes after a retry** is the output of that design, and it is asserted by a
test that would fail if the design were removed.

**Grounding an extraction is not the same as trusting it.** An agent that obeys
an instruction hidden inside a document will cite a genuine span for the value
it was told to produce — the provenance check passes, because the provenance is
real. So the injection defence had to sit somewhere else entirely: a tainted
document can never reach an agent-resolvable state key, and the extraction is
skipped rather than second-guessed. That separation is now a contract, not a
convention.

**A commercial safety filter is not automatically better than the check you
already have.** Before adopting a managed prompt-injection filter, it was
measured against this project's own corpus at the most sensitive setting
available. It returned no match on the planted injection fixture that the
existing check catches with five findings, and the reason turned out to be
context dilution — the payload matches on its own, survives 89 characters of
prepended certificate prose, and dies at 108. It was turned down on that
evidence and the measurement is published alongside the decision. One of its
filters did work well, and that one remains a candidate.

**Evidence rots quietly.** A committed evaluation score outlived the behaviour
it graded by two days without anything looking wrong, and a routine cleanup once
deleted the live records that were the only proof of a contract about deployed
state. Both taught the same lesson: a proof that names one hardcoded record can
be destroyed by ordinary maintenance. The harness now discovers what it verifies
rather than citing it, re-executes the tests it cites on every run, and re-reads
the evidence files it cites instead of trusting a number written next to them.

**So every number in this submission has a checker.** The graded domain suite
runs **24/24** at a **100% mean score** on a deterministic pass metric — not an
LLM judge grading prose, but a check on whether the enforcement outcome was the
required one. **549 passed** of 550 contract and unit tests, re-executed by the run
that reports them rather than quoted from a previous run. The one
failure is disclosed rather than trimmed: a console query that lists
failed commands newest-first can push an undated row off the page once
enough rows accumulate in a shared test database. It is a real finding
about ranking, it is being tracked, and it writes nothing. Nine contracts —
closed loop, duplicate and out-of-order events, provenance failure, injection
refusal, stale and double approval, forbidden agent-to-tool edges, one ERP write
after a retry, cataloging visible, and the eval suite — are re-verified at
capture time. And a claim ledger in `docs/proof/` binds every public number,
including all of the above, to the evidence file and field that produced it, so
a number that drifts from its source is reported rather than published.

## Built With

`google-adk` · `gemini-3.6-flash` · Agent Runtime · Agent Registry · Agent
Platform Sessions · Cloud Run · Firestore · Pub/Sub · Cloud Trace · Cloud
Logging · Cloud IAP · Secret Manager · Private Service Connect · Compute Engine
· Python · ERPNext · yente · Elasticsearch · OpenTelemetry
