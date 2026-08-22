# Submission video — narration script and shot list

Target 3:40–3:45; hard cap 4:00. One continuous unedited live segment,
0:15–2:25. Narration ~521 words at ~139 wpm.

Lines marked **SYNC** must land on the named on-screen event.

Public vocabulary only: Mission, Payload, Ground Control, Station-keeping,
Telemetry. Everything else is called what it is.

## Status of this draft

Three claims were corrected against the code before this file was written. See
"Corrections applied" at the end — do not restore the original wording.

Every bracketed token must be refilled from the frozen run before recording.
The scoreboard below still cites a 430-test suite; the suite is now 531.

## 1. Narration

### Beat 1 — The twist (0:00–0:15) — 31 words

> Every supplier-onboarding tool retires the day the ERP record is created.
> Keplaria stays. Two live suppliers, one unedited take: eighty-five seconds of
> machine work against the eleven minutes I timed by hand.

The qualifier is inside the claim — "I timed by hand" — spoken with confidence,
not apology. The overlay carries the full version.

### Beat 2a — Route one: the packet (0:15–0:35) — 42 words

> This is the deployed system, one continuous take. **SYNC (packet event
> appears)** A supplier packet arrives. The coordinator proposes a route —
> evidence plus compliance — but it doesn't decide. A deterministic policy layer
> validates the proposal against a versioned fleet catalog before anything runs.
> **SYNC (verdict chip)** Approved: two specialists.

### Beat 2b — The stop (0:35–0:55) — 43 words

> The compliance agent flags a sanctions near-match — and the system stops.
> **SYNC (park timer)** Fourteen seconds in, the case — a Payload, in our
> terms — parks in Ground Control, the human-approval dashboard, with its two
> ERP writes sitting unexecuted. It doesn't ask permission. It pauses, and
> waits.

### Beat 2c — The human (0:55–1:15) — 36 words

> One human, one decision. Twenty-three seconds of reading, one click to
> approve — **SYNC (ERP rows appear)** and two and a half seconds later the
> supplier record and its evidence attachment appear in the ERP. Nothing was
> written before that click.

### Beat 2d — Supplier two, and the honesty line (1:15–1:45) — 52 words

> Supplier two is a separate case — the approval you just saw doesn't carry
> over, and doesn't need to. The same policy gate reads this one clean: no
> near-match, no reason to stop, so nobody does. **SYNC (extraction panel)**
> Fields lift straight out of the document into the ERP, none of them rekeyed.
> Now watch a simulated year compress into the next eighty seconds.

Dead-air pocket 2 lives inside this beat's 43.9-second onboarding wait.

### Beat 2e — Route two: the clock (1:45–2:05) — 57 words

> Two clock events — and clock events engage no agents at all; deterministic
> policy handles them. **SYNC (first clock chip)** First renewal check: not yet
> due, so nothing happens — correctly. **SYNC (email sent)** Second: due, and a
> real renewal email goes out to the supplier. It goes unanswered — the evidence
> is now overdue — **SYNC (hold applied)** and a purchasing hold lands in the
> ERP. This supplier genuinely cannot be bought from.

### Beat 2f — Route three: the certificate (2:05–2:25) — 62 words

> **SYNC (certificate event)** Then the certificate comes back. New event, new
> route: evidence only, no compliance; policy agrees. **SYNC (grounding view)**
> Every field traces to a verbatim span in the document. **SYNC (hold lifts)**
> The hold releases. Renewal, hold, release — automatic corrections we call
> station-keeping. And what the supplier had to do was answer an email: no
> portal, no login, no account.
>
> **SYNC (run freezes on end state)** Three hundred eighty simulated days. Five
> under real hold. Zero human touches.

### Beat 3 — Architecture (2:25–2:55) — 73 words

> Six boxes: events in; the coordinator proposes; the policy gate decides,
> against a versioned fleet catalog; specialists reason; an outbox executes — and
> Ground Control can pause the flow before the ERP. Three departments —
> procurement, compliance, finance — each with a permitted-agent and a
> permitted-command list; finance's events engage no agents, and an out-of-list
> request is refused and recorded. All of it deployed: ADK agents with Gemini, on
> Agent Runtime, Cloud Run, Firestore, and Pub/Sub.

### Beat 4 — Failure and safety (2:55–3:30) — 88 words

> The part most demos skip: failure. **SYNC (503 injected)** We force a 503 from
> the ERP mid-write. Bounded retry — and afterwards, exactly one supplier record
> exists. Zero duplicates. **SYNC (injected doc)** We plant a prompt injection in
> a document; the agent refuses, and the refusal is logged. **SYNC (red field)**
> Then an agent returns a worker count that's schema-valid but appears nowhere in
> the source. Every value must resolve to a verbatim span; this one doesn't —
> caught, retried within bounds, parked for a human, zero ERP writes. It's all in
> Telemetry — our OpenTelemetry traces. Not perfect. Contained.

### Beat 5 — Close (3:30–3:45) — 38 words

> Six of six steps autonomous. Twenty-two fields across the run, none rekeyed.
> One intervention — required by policy, not failure. Eighty-five seconds against
> six-sixty-three: a baseline I timed by hand, not yet practitioner-reviewed.
> Keplaria. Onboarding that doesn't end. Links below.

## 2. Shot list

Persistent during 0:15–2:25: a "LIVE — one take" badge, and a run clock counting
up against a visible "budget: 130 s" mark.

Split-screen layout for the live segment:

- Left ~55%: Ground Control, labelled once on first appearance —
  "Ground Control — human-approval dashboard".
- Right top: the ERPNext supplier list, live.
- Right bottom: the route strip — incoming event chip, the coordinator's
  proposed roster, the policy verdict. This strip is what makes the three routes
  legible: it visibly shows 2 agents / 0 agents / 1 agent across the segment.
  **Depends on the console change below.**

| Beat | On screen | Overlay text |
|---|---|---|
| 1 | Black card: "Onboarding ends when the ERP record is created." — "ends" strikes through, replaced by "begins". Title card: Keplaria. | "85.1 s machine vs 663.5 s by hand — 20-step manual walkthrough, author-timed, not practitioner-reviewed" |
| 2a | Packet event chip; route strip: proposed "evidence + compliance", verdict "APPROVED 2/2" | "Deployed on Google Cloud · unedited from here" |
| 2b | Compliance result "sanctions near-match"; case moves into Ground Control's parked column showing `create_supplier — HELD`, `attach_evidence — HELD` | "Parked in 14.1 s · ERP writes so far: 0" |
| 2c | Cursor reads the case, clicks Approve; ERP pane: supplier row and attachment appear | "Human decision: 23 s · Execution: 2.5 s" |
| 2d | New case card labelled as a separate Payload; route strip runs again; extraction panel fills; ERP row appears; simulated-clock widget starts | "Separate case — the approval does not carry over" |
| 2e | Sim-clock jumps; chips: `renewal_due → no action`, `renewal_due → renewal email sent` (show the outbound Communication in the ERP), `evidence_overdue → purchasing hold`; ERP row flips to Hold | "Clock events: agents engaged — none (policy only)" |
| 2f | `certificate_received` event chip carrying the renewed document; route strip "evidence only → APPROVED 1/1"; grounding view highlights the verbatim spans; ERP hold lifts; freeze on end state | "380 simulated days · 5 enforced hold days — hold AND release both executed in ERP · 0 human touches", then "Design intent: the supplier answers an email. No portal, no login, no account." |
| 3 | Full-screen six-box diagram, arrows animating in flow order; then a three-row department table with permitted-agent / permitted-command columns; deployment strip | "ADK + Gemini · Agent Runtime · Cloud Run · Firestore · Pub/Sub" and "10/10 deployed rehearsals under the 130 s budget" |
| 4 | Three fast panels: (1) trace of 503 → retry → ERP count = 1; (2) document with highlighted injection and the refusal in the trace; (3) extraction output with the worker-count field flagged "no source span" → retry → parked card in Ground Control. Trace viewer labelled "Telemetry — OpenTelemetry traces" | "0 duplicate writes after a forced retry" · "Refusal recorded" · "0 ERP writes" |
| 5 | Scoreboard card, judge-accessible Ground Control URL, repo link | "6/6 steps autonomous · 19 of 20 manual steps eliminated · 22 fields, 0 rekeyed · 1 policy-required intervention · 85.1 s vs 663.5 s (author-timed, not practitioner-reviewed)" |

## 3. Six-box diagram

1. **Triggers** — supplier packet · clock event · certificate
2. **Coordinator** — proposes the agent roster
3. **Policy Gate** — decides, against the versioned fleet catalog
4. **Specialists** — evidence · compliance
5. **Ground Control** — human approval when policy requires it
6. **Outbox → Executor → ERP** — the only path to a side effect

Arrows: 1 → 2 (event) · 2 → 3 (proposal) · 3 → 4 (approved roster; refusals
recorded at 3) · 4 → 6 (validated commands) · 3 ⇢ 5 (dashed: the case parks when
policy requires a decision) · 5 → 6 (approval releases the held commands).

Carrying sentence: "Agents propose, a deterministic gate decides, and nothing
reaches the ERP except through the outbox — with Ground Control able to pause any
case on the way."

## 4. Dead-air pockets

**Pocket 1** — anywhere in 0:20–0:55, while the agents are working:

> While it works: nothing you're watching can write to the ERP directly. Every
> action queues as a command in a Firestore outbox, and an executor performs it
> only after policy has signed it off.

**Pocket 2** — during the 43.9-second onboarding of supplier two:

> Ten consecutive rehearsals of this run on the deployed system, all under the
> hundred-thirty-second budget, before we pressed record. What you're watching is
> the ordinary behaviour, not the lucky take.

## 5. Lines rejected as overclaims

1. "Watch one approval unlock a year of autonomous governance." False — the
   approval binds to one version of one case. Replaced with "Supplier two is a
   separate case — the approval you just saw doesn't carry over, and doesn't need
   to." The honest version is the better line: one gate, shown discriminating.
2. "87% faster than manual onboarding, validated against industry practice."
   Replaced with the raw seconds and the qualifier. No percentage is spoken.
3. Any supplier-admin persona, quote, or testimonial. No such user was
   interviewed. Replaced with the mechanism plus an on-screen "Design intent"
   label.
4. "Keplaria blocked a sanctioned supplier." The system does not adjudicate
   sanctions; it detected a near-match and stopped.
5. "Zero-hallucination extraction." Replaced by the mechanism and a shown
   failure: containment demonstrated beats perfection asserted.
6. "The hold saved five days of risk exposure." Nothing was measured about
   avoided purchases. Replaced with "five under real hold", and the overlay says
   why it can be claimed: hold and release both executed.
7. "Fully autonomous — no humans needed", and its mirror "it never fails."
   Replaced with "One intervention — required by policy, not failure", and a
   35-second segment about how it fails.

## Corrections applied to the first draft

1. **No inbound email exists.** The draft had the certificate arrive as "a plain
   email reply", with an inbox on screen. `certificate_received` is a published
   event carrying a document reference (`spikes/judge_run/harness.py:84`); no
   mail is ingested. The outbound renewal message is real — it creates a
   Communication with `send_email: 1` (`app/executor/frappe.py`) — so the email
   goes *out*, and the reply is modelled as an event. Beat 2f and its shot were
   rewritten to say only what runs.
2. **The 22 fields are run-wide, not supplier two's.** `fields_without_rekeying`
   sums across every command in the run (`app/metrics.py:206`). Moved to the
   close as a run total.
3. **No redaction step exists.** Not in this draft, but the older plan called for
   showing "pixel redaction" on screen. `RedactedDerivative` is a type name;
   `app/documents.py` states the real preprocessor comes later. Documents are
   page-text fixtures. Nothing on camera may imply OCR, scan handling, or
   multimodal extraction.

## Console change this script depends on

The route strip needs the coordinator's proposal shown next to the policy
verdict. `console/projection.py` already carries `proposed`, `route`, `dropped`
and `refused`, but not `added`; `console/templates/case.html` renders only
`route`, `dropped` and `refused`. Two small additions make routing completion
visible.

This is a console-only deploy. The ten-run streak binds the engine and
`keplaria-ingress` only — `spikes/run_streak/evidence.json` lists the review
service under `not_exercised`, and the console is not on the timed path — so it
does not restart the streak.
