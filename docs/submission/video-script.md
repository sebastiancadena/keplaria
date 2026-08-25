# Submission video — narration script and shot list

Target 3:40–3:45; hard cap 4:00. One continuous unedited live segment,
0:15–2:25. Narration 467 spoken words (the optional pocket is a substitute, not additive). Every
beat is rendered through `video/narrate.sh` at its slot; a clamp warning in
the FAST direction means cut the copy, never speed the voice. A warning in the
SLOW direction (the voice under-fills the slot) is silence over live UI, which
is fine.

Rendered 2026-08-25 against the slots below: live-segment beats land at
123–148 wpm with silence to spare; beat 3 at ~154, beat 4 at ~147, the close at
~144. Nothing needed a fast clamp.

Lines marked **SYNC** must land on the named on-screen event.

Public vocabulary only: Mission, Payload, Ground Control, Station-keeping,
Telemetry. Everything else is called what it is.

## Status of this draft

Rewritten 2026-08-25 (day 14) after an external script review and the
organizer pre-submission check-in. Six claims have now been corrected against
the code; see "Corrections applied" at the end — do not restore the original
wording. The judge's stated priorities for this track (security, recovery
steps, going beyond the happy path, "clever, not long-running") are addressed
in beats 3 and 4 with evidence that already exists; nothing new is built.

Every bracketed token must be refilled from the frozen run before recording.
Numbers below were measured against the frozen deployment (`f972ce6`) and are
bound in `docs/proof/claims.toml`; run `scripts/claim_ledger.py --check` after
any edit.

## 1. Narration

### Beat 1 — The twist (0:00–0:15) — 26 words

> Supplier onboarding usually ends when the ERP record is created. Keplaria
> stays for what happens months later. Two synthetic suppliers, one real ERP,
> one unedited take.

The baseline comparison moves to the overlay with its qualifier attached. It
is the weakest-provenance number in the video (author-timed) and should not be
the first thing a judge hears; the twist should.

### Beat 2a — Route one: the packet (0:15–0:35) — 39 words

> This is the deployed system, one continuous take. **SYNC (packet event
> appears)** A supplier packet arrives. The coordinator proposes a route,
> evidence plus compliance, but it doesn't decide. A deterministic policy gate
> checks the proposal against a versioned fleet catalog. **SYNC (verdict chip)**
> Approved: two specialists.

### Beat 2b — The stop (0:35–0:55) — 39 words

> The compliance agent flags a sanctions near-match, and the system stops.
> **SYNC (park timer)** Fourteen seconds in, the case, a Payload in our terms,
> parks in Ground Control, the human-approval dashboard, its two ERP writes
> held, unexecuted. It doesn't ask. It waits.

### Beat 2c — The human (0:55–1:15) — 21 words

> One human, one decision. **SYNC (ERP row appears)** When I approve, the held
> supplier write is released into ERPNext. Nothing was written before that
> click.

The stand-in attachment is not narrated. The proof is the supplier row and
the click that preceded it.

### Beat 2d — Supplier two, and the honesty line (1:15–1:45) — 61 words

> Supplier two is a separate case; the approval doesn't carry over. This one
> reads clean: no near-match, so nobody stops it. **SYNC (extraction panel)**
> Its fields come from a synthetic page-text fixture, no OCR, into the ERP
> without rekeying. The supplier's side of this, by design, is answering an
> email: no portal, no account. Now, a simulated year and a half in eighty
> seconds.

This beat holds the 21.4-second onboarding wait, which is why it carries the
data-boundary sentence and the overlooked-user line. Spoken once, plainly, no
persona.

### Beat 2e — Route two: the clock (1:45–2:05) — 41 words

> Two clock events; no agents, policy alone. **SYNC (first clock chip)** First
> check: not yet due, nothing happens, correctly. **SYNC (email sent)** Second:
> due, and a real renewal email goes out. Unanswered. Evidence overdue, **SYNC
> (hold applied)** and a purchasing hold lands in the ERP. This supplier cannot
> be bought from.

### Beat 2f — Route three: the certificate (2:05–2:25) — 41 words

> **SYNC (certificate event)** The renewed certificate enters as a published
> event; policy routes it to evidence only. **SYNC (grounding view)** Every
> field traces to a verbatim span. **SYNC (hold lifts)** The hold releases.
> Renewal, hold, release: station-keeping.
>
> **SYNC (run freezes on end state)** Three hundred eighty simulated business
> days. Five under real hold. Zero human touches.

### Beat 3 — Architecture and the security boundary (2:25–2:57) — 82 words

> Six boxes: events in, the coordinator proposes, a deterministic gate decides
> against a versioned fleet catalog, and only the outbox reaches the ERP;
> Ground Control can pause it first. That boundary is the security model:
> agents hold no ERP credential; the one executor runs as a scoped ERP role
> that cannot delete; Firestore, not the model, remembers the case for months.
> Three departments, permitted agents and commands each: that table is the
> fleet. On Google Cloud: the registered graph, and one trace.

Every sentence here is backed by a committed proof: the credential boundary by
`spikes/frappe_scoped_executor/evidence.json` (role read back off the live
site: no delete, no role widening, no financial documents) and the
`forbidden_agent_tool_edges` core contract; the durable-state line by the
Firestore-owned case version and outbox. "Memory" is said once and means
durable case state; nothing implies the agents learn.

### Beat 4 — Failure, recovery, and safety (2:57–3:30) — 81 words

> The part most demos skip: failure. **SYNC (command ledger)** A failed ERP
> command stays durable; when the destination is repaired, the unattended sweep
> re-drives that same command to done: one record, no duplicate. **SYNC
> (injected doc)** This planted instruction is caught by a deterministic scan
> and blocked before any agent sees it: zero ERP writes. **SYNC (red field)**
> And a schema-valid worker count with no source span is rejected, retried
> once, and parked for a human: zero ERP writes. Every refusal lands in
> Telemetry, our OpenTelemetry traces. Not perfect. Contained.

Three panels, not four: the replay-safety contract is real but a fourth panel
in 35 seconds cannot be read. It stays on `/proof`.

### Beat 5 — Close (3:30–3:45) — 36 words

> Kepler didn't discover that planets move; he showed their motion obeys law.
> Launch a case once, and it stays up for months, corrected by policy, not by
> you. Put your work in orbit. Keplaria dot com.

**The scoreboard on the overlay carries three ideas, not six.** Fifteen seconds
cannot hold six figures and land the name. The full figures, qualifiers
included, live on `keplaria.com/proof`, which is in frame.

## 2. Shot list

Persistent during 0:15–2:25: a "LIVE — one take" badge, a run clock counting
up against a visible "budget: 130s" mark, and the console's `.run.app` address
bar in the **hash form** (`keplaria-console-bklu5jcdea-uc.a.run.app`), legible,
so the Google Cloud deployment is on screen for the whole live window rather
than asserted later.

Split-screen layout for the live segment:

- Left ~55%: Ground Control, labelled once on first appearance —
  "Ground Control — human-approval dashboard".
- Right top: the ERPNext supplier list, live, cropped to the data region
  (masthead out of frame unless the trademark call in `video-audit.md` is
  recorded the other way).
- Right bottom: the route strip — incoming event chip, the coordinator's
  proposed roster, the policy verdict. This strip is what makes the three
  routes legible: it visibly shows 2 agents / 0 agents / 1 agent across the
  segment. Shipped in the console (`f9a3233`); no dependency remains.

| Beat | On screen | Overlay text |
|---|---|---|
| 1 | Black card: "Onboarding ends when the ERP record is created." — "ends" strikes through, replaced by "begins". Title card: Keplaria. | "55.3 s of machine time vs 663.5 s by hand — a 20 steps walkthrough, 19 of which the run removes; author-timed, not practitioner-reviewed" |
| 2a | Packet event chip; route strip: proposed "evidence + compliance", verdict "APPROVED 2/2" | "Deployed on Google Cloud · unedited from here" |
| 2b | Compliance result "sanctions near-match"; case moves into Ground Control's parked column showing `create_supplier — HELD`, `attach_evidence — HELD` | "Parked in 13.8 s · ERP writes so far: 0" |
| 2c | Cursor reads the case, clicks Approve; ERP pane: supplier row appears | "47.7 s human approval · 0.1 s execution" |
| 2d | New case card labelled as a separate Payload; route strip runs again; extraction panel fills; ERP row appears; simulated-clock widget starts | "Separate case — the approval does not carry over", then, over the extraction panel: "Synthetic page-text fixture · no OCR · the returned certificate enters as a published event", then "Design intent: the supplier answers an email. No portal, no login, no account." |
| 2e | Sim-clock jumps; chips: `renewal_due → no action`, `renewal_due → renewal email sent` (show the outbound Communication in the ERP), `evidence_overdue → purchasing hold`; ERP row flips to Hold | "Clock events: agents engaged — none (policy only)" |
| 2f | `certificate_received` event chip carrying the renewed document; route strip "evidence only → APPROVED 1/1"; grounding view highlights the verbatim spans; ERP hold lifts; freeze on end state | "380 simulated business days · 5 enforced hold days — hold AND release both executed in ERP · 0 human touches" |
| 3 | (1) Full-screen six-box diagram, arrows animating in flow order. (2) **Google Cloud proof, sanitized:** the deployed graph in Agent Registry / Agent Runtime, then the Cloud Run services list, then one Cloud Trace belonging to supplier one's case — each capture passes the browser-chrome checklist in `video-audit.md` (no project number, no account, hash-form URLs only). (3) The `/fleet` scope matrix (three departments, permitted-agent / permitted-command columns, exercise counts); cross-fade to supplier one's Routing panel as "that table is the fleet" lands. | "Agents: no ERP credential · Executor: one scoped ERP role, read back off the live site — cannot delete, cannot widen its role" then "ADK + Gemini · Agent Runtime · Agent Registry · Cloud Run · Firestore · Pub/Sub · Cloud Trace · IAP" and "10/10 deployed rehearsals under the 130 s budget, 2 cold starts" |
| 4 | Three panels: (1) the command ledger for the sweep probe: `clear_hold` `failed` → re-driven by the unattended sweep → `done`, beside the ERP supplier count for that name = 1 (source: `spikes/core_contracts/evidence.json`, check `retried_erp_write_is_singular`); (2) the injected fixture with the planted instruction highlighted, the case's `injection` block (`tainted: true`, pattern id, page) and the policy outcome `DOCUMENT_INJECTION → blocked`, then the trace; (3) extraction output with the worker-count field flagged "no source span" → retry → parked card in Ground Control. Trace viewer labelled "Telemetry — OpenTelemetry traces" | "Failed command re-driven by the sweep · one ERP record" · "Deterministic scan · blocked before any agent · 0 ERP writes" · "Grounding: no span, no write · 0 ERP writes" |
| 5 | Scoreboard card, `keplaria.com` in frame (front door + `/proof` verification ledger), repo link | "380 simulated business days · 1 policy-required intervention · real ERP hold and release" with a second line, smaller: "22 fields, 0 rekeyed · full ledger and qualifiers at keplaria.com/proof" |

Every panel in beats 3 and 4 is real output: a trace, a ledger row, a test
result, or a console page. The six-box diagram is the one designed
illustration, and it is labelled as a diagram.

## 3. Six-box diagram

**Built:** `docs/architecture/judge-diagram.svg`, generated by
`docs/architecture/build_judge_diagram.py` on the 1920×1080 video frame; the
PNG beside it is a browser render. Doctor byte-compares the SVG against its
build, so a box that stops matching the code turns the check red. Nothing
carrying meaning sits below 28px — this is not a shrunk `architecture.svg`.

**Animated:** `video/judge-diagram.mp4` (17.0s, 1920×1080), from
`video/judge_diagram_scene.py` rendered with the Manim kit in
`~/dev/git/byteql-video` (`uv run --group animation scripts/render.py`).
It assembles in the narration's order: walls and zones first, then each box
as its clause is spoken, with the single ERP crossing drawn LAST — after the
pause point, matching the line "Ground Control can pause it first". **This
clip is outside the continuous-unedited window (0:15–2:25), which is the only
reason it may exist.** If it is cut, the static SVG stands in with no other
change — that is also its rollback.

**The device:** two walls, each pierced exactly once. The Policy Gate is
embedded in the first; a single heavy arrow crosses the second. "Only the
gate decides" and "only the outbox reaches the ERP" are read as shape, not
as caption.

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

## 4. Dead-air pocket

One pocket, and it is a **substitute**, never additive copy: if beat 2a or 2b
renders short of its slot, this fills the gap; otherwise it is not spoken.

**Pocket 1** — anywhere in 0:20–0:55, while the agents are working:

> While it works: nothing you're watching can write to the ERP directly. Every
> action queues as a command in a Firestore outbox, and an executor performs it
> only after policy has signed it off.

The former pocket 2 (ten rehearsals) is an overlay in beat 3 now; its 21-second
home in beat 2d is taken by the data-boundary and email lines.

## 5. Lines rejected as overclaims

1. "Watch one approval unlock a year of autonomous governance." False — the
   approval binds to one version of one case. Replaced with "Supplier two is a
   separate case; the approval doesn't carry over." The honest version is the
   better line: one gate, shown discriminating.
2. "87% faster than manual onboarding, validated against industry practice."
   Replaced with the raw seconds and the qualifier, on the overlay only. No
   percentage is spoken.
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
8. "We force a 503 from the ERP mid-write; bounded retry; zero duplicates."
   No such probe exists. `spikes/retry_503` retries a document-dependency fetch
   and writes a redacted derivative, not an ERP record; the judge run reports
   `commands_retried_then_succeeded: 0`. Replaced with the retried ERP write
   that actually happened: the sweep probe's `clear_hold` failed once and was
   re-driven to `done` by the deployed sweep, leaving one record
   (`spikes/core_contracts/evidence.json`, `one_erp_write_after_retry`).
9. "The agent refuses the prompt injection." The agent never sees it. A
   deterministic scan taints the page before any agent reads it, the pages are
   withheld from agent-resolvable state, and `DOCUMENT_INJECTION` forces the
   gate to blocked (`app/nodes.py`, `app/injection.py`). The scan is a
   heuristic over the planted fixture, not a general defence, so the spoken
   scope is "this planted instruction", never "prompt injection is blocked".
10. "Two live suppliers." The ERP is real; the suppliers are synthetic fixtures.
    "Two synthetic suppliers, one real ERP."
11. "Self-improving agents" / "the agents learn from memory." Not built, and
    deliberately so: agent history lives in Sessions and is never a compliance
    fact. The judge asked about memory; the honest answer is that Firestore
    remembers the case and the model does not decide what is true.

## Corrections applied

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
   multimodal extraction. **2026-08-25:** the beat 2d wording "fields lift
   straight out of the document" still implied a document pipeline; now the
   narration and an overlay say "synthetic page-text fixture, no OCR".
4. **The ERP 503 scene (2026-08-25).** See rejected line 8. The planning
   notes carried the same story and were corrected the same day.
5. **Injection attribution (2026-08-25).** See rejected line 9.
6. **Google Cloud is shown, not asserted (2026-08-25).** The rules ask the video
   to demonstrate the backend runs on Google Cloud. The hash-form `.run.app`
   address stays in frame for the live window, and beat 3 shows the registered
   graph, the Cloud Run services and one Cloud Trace, each capture audited
   against `video-audit.md` before the take.
