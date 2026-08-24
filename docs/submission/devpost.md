# Devpost submission copy

Paste-ready text for the Devpost submission form, one section per field. It
lives here rather than only in the form so that every number in it is bound to
the run that produced it: `docs/proof/claims.toml` cites this file, and
`uv run python scripts/claim_ledger.py --check` re-reads each evidence file and
fails if the prose and the evidence disagree. **Edit the number here and the
check will catch you; edit the evidence and it will catch that too.**

The headings below are the Devpost form's own. The [Official
Rules](https://allthingsagentichackathon.devpost.com/rules) additionally
require four things of the description, and each has a home here: features
and functionality (What it does), technologies used and other data sources
(How we built it), findings and learnings (Challenges, What we learned).

Two things are deliberately absent and must not be invented before they exist:
the demonstration video URL and the frozen commit the submission cites.

---

## Tagline

> Every onboarding tool retires the day the ERP record is created. Keplaria
> stays: it wakes months later to chase the expiring certificate, holds
> purchasing when the evidence lapses, and releases the hold when the renewal
> checks out. The model proposes; deterministic policy decides.

## Category

**Fortified Enterprise Fleet.**

## Links

| Field | Value |
|---|---|
| Project home | <https://keplaria.com> — the front door; <https://keplaria.com/proof> binds every published number to the run that produced it |
| Try it out — case console | <https://keplaria-console-bklu5jcdea-uc.a.run.app> (no sign-in; cases grouped by supplier, and the fleet page for the rulebook with live counts) |
| Try it out — review console | <https://keplaria-review-bklu5jcdea-uc.a.run.app/review> (Google sign-in through Cloud IAP; the two organizer accounts are pre-authorized) |
| Code repository | <https://github.com/sebastiancadena/keplaria> |
| Demonstration video | TODO — record day 17, add before submitting |
| Architecture diagram | uploaded as a submission image; it is generated from committed sources in the repository, so it cannot drift from the deployed system |

---

## Inspiration

A beverage manufacturer needs a new packaging supplier approved before the
Q4 run. Any agent can do that in a minute: read the packet, screen the name,
create the record. Then the certificate of insurance expires in March, and the
agent that created the record is long gone. Someone has to notice, chase the
renewal, and decide whether it is still safe to buy. Every onboarding tool we
looked at retires the day the ERP record is created, and the ongoing work goes
back to a person with a calendar reminder.

That gap is the whole project. Approval was never the hard part; the hard
question is whether an agent can stay accountable for an obligation across
months. The name is the answer we wanted: Kepler's breakthrough was not
noticing that planets move, it was showing that their motion obeys law. An
agent that runs for minutes can improvise. One that stays for months has to
obey policy, and the policy has to be something you can read.

## What it does

**Keplaria stays.** One durable case per supplier onboards it into a real ERP,
then wakes months later on its own clock: it emails the supplier for renewed
evidence, places a reversible purchasing hold when the certificate lapses,
reads the renewal that eventually arrives, checks every extracted value against
the document it came from, screens the name again, scores the case against a
versioned policy, and releases the hold. None of that is a chat. The system
decides, and it writes those decisions into the ERP.

**Two nouns carry the design.** The **fleet** is the crew and its rulebook:
three departments, a coordinator that proposes, two specialist agents
(evidence and compliance), and the five ERP commands they may issue. A
**payload** is one supplier's case, carried through that fleet for months. The
coordinator model proposes which specialists an event needs; a deterministic
policy layer decides, against a versioned catalog, and records what it dropped
or added. The public console shows both: every case grouped under its
supplier, and the fleet's rulebook with a live count of how many cases
exercised each rule.

**It stops exactly where policy requires a person, and nowhere else.** A
sanctions near-match parks the case for a reviewer at **Ground Control**, the
human decision console, with its ERP writes queued but unexecuted. One
reviewer, one click, and the record appears in the ERP the same moment. A
rejection is equally binding. A protective hold never waits for anyone: it is
already applied before a reviewer opens the page, because refusing to hold a
risky supplier during a review would invert the point of the review.

**The other side of the relationship is the one nobody builds for.** The party
who has to keep a certificate current is often a small supplier with no
compliance department and no room for one more portal. Keplaria asks nothing
of them: no portal, no login, no account, no training. The renewal request is a
real email sent by the deployed workflow. One boundary is stated rather than
implied: in this prototype the returned certificate enters as a published
event; ingesting the reply from a mailbox is design intent, not built, and the
video labels it that way on screen.

**Measured, not asserted.** One deployed run of the whole lifecycle, two
suppliers (one parks for a human, one runs unattended from onboarding to hold
release), with every figure below read from the run's own evidence file in
the repository:

- **55.3 s of machine time** against a **130s** budget, plus a single
  **47.7 s human approval**, timed separately because it is a person's time.
- By hand, the same work took **663.5 s** over **20 steps**,
  **19 of which the run removes**; the twentieth is the approval policy
  requires. Author-timed, not practitioner-reviewed; labelled that way
  everywhere it appears.
- **22 fields** written to the ERP with nobody retyping them.
- **1 policy-required intervention**, and the system stopped for it rather than
  deciding it.
- **5 enforced hold days**: claimed only because both the hold and its release
  executed in the ERP.
- **0 duplicate writes after a retry.**
- **380 simulated business days**, about a year and a half of a supplier's
  life, inside one recording; the compression is disclosed on screen.

## How we built it

**Two runtimes, one boundary.** The agent graph is built on the **Google Agent
Development Kit (ADK)** for Python, runs on **Gemini 3.6 Flash**, and is hosted
on **Agent Runtime** as a reasoning engine: it auto-registers in **Agent
Registry**, keeps execution state in **Agent Platform Sessions**, and reaches a
private screening VM over **Private Service Connect** (no external IP, not
reachable from the internet). Three **Cloud Run** services surround it: an
authenticated **Pub/Sub** push adapter, the only component that can drive a
state change through the graph; the public read-only case console; and the
review console behind **Cloud IAP**, where the reviewer's identity is read from
a signed assertion, never from a header a caller could set.

**Separation of concerns is enforced, not described.** The fleet catalog is one
committed, versioned artifact: every agent, the route for every event type, and
the agents and commands each department may use. The same file that the
console renders is the file routing enforces, so what a judge sees cataloged is
literally what runs. A proposal that reaches across a department boundary is
refused and recorded, never quietly corrected; a catalog that fails to load
refuses every proposal rather than falling back to stale data. Each specialist
has its own context and tool surface, and no agent holds a credential.

**State is durable and replay-safe.** Authoritative case state lives in
**Firestore**, transactionally: the case version, the event claim that makes
duplicate and out-of-order events impossible, the approval keyed to the case
version it was taken against, and the command outbox whose ids derive from the
case and its cycle, so a retried command leaves exactly one ERP record. The one
stated exception is outbound mail, which the ERP does not deduplicate: a lost
response can repeat a message, never a record. Generative memory is
deliberately not trusted with compliance facts.

**Credentials are scoped and confined.** Secrets come from **Secret Manager**;
each service runs as its own service account; the ERP credential belongs only
to the deterministic executor, never to an agent, and to a purpose-made ERP
user whose single role can read, write and create supplier records and create
correspondence and attachments, and nothing else: it cannot delete what it
created, widen its own permissions, or read an invoice. Those limits are
measured against the live site on every run of the check.

**Failures are handled where they happen.** Bounded retries with a dead-letter
topic and an unattended sweep; a schema-valid but source-unsupported worker
value is rejected, retried within bounds, and quarantined for a human instead
of reaching the ERP. Traces go to **Cloud Trace** and are load-bearing: a
10.6 s rise in run time was diagnosed node by node against them and traced to
model reasoning length, not code.

**The enterprise systems are real; the data is not.** A hosted **ERPNext** site
holds the supplier records and a self-hosted **yente** screening service
answers the sanctions calls, but every document, supplier, and watchlist
entity is synthetic and was authored for this project; nothing is customer
data or a de-identified derivative of it. Case documents are authored
fixtures stored as the redacted page-text derivative the pipeline is
contracted to produce, and every field extracted from them must resolve to a
verbatim span or the case quarantines. The screening index is a synthetic
watchlist in the FollowTheMoney format; no OpenSanctions content is indexed
(the publisher confirmed in writing that bulk download would have been
permitted; indexing the fixture instead is a determinism choice). The ERP is a
dedicated demonstration site, and every mutation is sandboxed to it.
Third-party code, assets, and AI assistance are itemised in the repository;
its README maps each of the track's seven platform subsystems to native use, a
first-party equivalent, or a deliberate, measured decision not to use it.

## Challenges we ran into

**The hard problem was never the agents; it was making their work safe to
replay.** Every serious failure in this build had the same shape: an event
arrives twice, a process dies mid-write, a retry fires after the first attempt
already succeeded. Correct-looking agent code plus an at-least-once event system
produces duplicate suppliers and approvals that apply twice. The fix was to stop
treating durability as a property of the agent and make it a property of the
state. **0 duplicate writes after a retry** is the output of that design, and a
test would fail if the design were removed.

**Grounding is not trust.** An agent that obeys an instruction hidden in a
document will cite a genuine span for the value it was told to produce, and the
provenance check passes, because the provenance is real. So the injection
defence had to sit elsewhere: a tainted document never reaches a state key an
agent can read.

**The engine cannot reach the internet, on purpose.** The graph runs behind a
private interface with no public egress, so the deterministic ERP executor had
to live in the Cloud Run ingress rather than in the graph. What began as a
workaround became the cleanest boundary in the system: the component that
reasons and the component that writes are different processes under
different identities.

**Evidence rots quietly.** A committed evaluation score once outlived the
behaviour it graded by two days, and a routine cleanup once deleted the only
live records proving a contract about deployed state. So the harness now
discovers what it verifies instead of naming it, and re-runs everything it
cites.

## Accomplishments that we're proud of

**Every number in this submission has a checker.** The graded domain suite
runs **24/24** at a **100%** mean score on a deterministic pass metric: whether
the enforcement outcome was the required one, not whether a model liked the
prose. **549 passed** of 550 contract and unit tests, re-executed by the run
that reports them; the one failure, later traced to a shared test database
rather than to the product, is disclosed rather than trimmed.
**Nine contracts**, from replay safety to one ERP write after a retry, are
re-verified at capture time. Ten consecutive deployed runs, two of them cold starts,
finished inside the budget before anything was recorded. And a claim ledger
binds every public number above to the evidence file and field that produced
it, so a number that drifts from its source is reported rather than published.

**A protective hold that does not wait.** Proven on the deployed system by a
reviewer signed in through IAP: the ERP hold was written eleven minutes before
the rejection that withheld everything else, both timestamps server-side.

**A console a stranger can read in ten seconds.** The case console was graded
by zero-context readers until one could say, unprompted, what the fleet is,
what a payload is, and how they relate. The record of those reads is in the
repository beside the rest of the evidence.

## What we learned

**A commercial safety filter is not automatically better than the check you
have.** A managed prompt-injection filter was measured against this project's
own corpus before being adopted; it missed the planted injection the existing
check catches, because a few lines of ordinary certificate prose ahead of it
dilute the match. Turned down on that evidence, measurement published.

**A model can read perfectly and still be unusable.** An open-weights Gemma 4
read **6 of 6** test documents correctly, including the one with a planted
instruction beside a decoy date, and returned the required output structure on
**0 of 6**, in three different shapes across identical calls. The same weights
run locally, where generation is constrained to the schema, scored full marks
on both. What differed was how the model was served, not how it read. Not
adopted; measurement published.

**A number you did not re-run is not evidence.** Twice a green result outlived
the thing it proved. The habit that fixed it, re-executing every proof the
moment it is cited, cost more than any feature and is the one thing from this
build we would carry into production first.

## What's next for Keplaria

Each of these is stated as a limit today, on the surface that would otherwise
overclaim it.

- **Ingest the supplier's reply.** The renewal request is a real outbound
  email; the returned certificate currently enters as a published event.
  Reading it from the mailbox is the first thing to build.
- **A real document preprocessor.** Documents are page-text fixtures; the
  PDF, OCR and redaction stage that would produce the same derivative from a
  scan is next.
- **A live pause on the review branch.** A parked case is durable state, not a
  suspended run; reinstating a genuine mid-run pause on the same branch is the
  next milestone.
- **Let quarantine restrict.** A blocked case claims nothing, so a supplier
  who becomes sanctioned after onboarding cannot yet be held; letting the
  quarantine terminal claim restrictive commands closes that.
- **A practitioner's eye on the baseline.** The manual walkthrough is
  author-timed; one review of the twenty-step list by a procurement or
  compliance practitioner would close the qualifier.

## Built With

`google-adk` · `gemini-3.6-flash` · Agent Runtime · Agent Registry · Agent
Platform Sessions · Cloud Run · Firestore · Pub/Sub · Cloud Trace · Cloud
Logging · Cloud IAP · Secret Manager · Private Service Connect · Compute Engine
· Python · ERPNext · yente · Elasticsearch · OpenTelemetry
