# Submission video — narration script and shot list

Target 3:40–3:45; hard cap 4:00. One continuous unedited live segment,
0:15–2:25. Narration 499 spoken words (the optional pocket is a substitute, not
additive).

**Written for the ear, one sentence per line (2026-08-27).** Each line below
is one utterance: it is synthesised in its own TTS request by
`video/narrate_beat.py`, placed on the take's clock, and judged on its own by
`video/listen.py` (round-trip transcription, pitch contour, and a blind
listener whose paraphrase is checked against the `.meaning` line kept beside
the beat). The first cut was written for the eye and split by what was on
screen; the voice read colons as list items, stacked appositives as separate
places, and a file that opened on "And..." or "Keplaria dot com." cold. The
rules that came out of that: one idea per sentence, a subject and a finite
verb in every line, no line opening on a conjunction, no colon-as-reveal, no
appositive longer than three words, and a fragment only after a blank line
(a long pause). A beat that will not fit its window is cut, never sped past
the engine's 1.12 rate clamp; the fitting is native `speakingRate`, not a
time-stretch.

Lines marked **SYNC** in the shot list must land on the named on-screen
event; the `.sync` files in the video repo anchor an utterance to a take
event, and `vo/plans/*.json` records where each one actually landed.

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

### Beat 1 — The twist (0:00–0:15) — 24 words

> Onboarding ends when the ERP record is created.
> Keplaria stays for what happens months later.
> Two synthetic suppliers, one real ERP, one unedited take.

The baseline comparison moves to the overlay with its qualifier attached. It
is the weakest-provenance number in the video (author-timed) and should not be
the first thing a judge hears; the twist should.

### Beat 2a — Route one: the packet (0:15–0:35) — 36 words

> A supplier packet arrives.
> The coordinator proposes two agents, evidence and compliance. It does not decide.
> The decision belongs to a deterministic policy gate, which uses a versioned fleet catalog.
> Approved. Two specialists go to work.

### Beat 2b — The stop (0:35–0:55) — 44 words

> The compliance agent flags a sanctions near-match. The system stops.
> After twelve seconds, the case is parked in Ground Control.
> We call a case a Payload.
> Both of its ERP commands are on hold, and neither has run.
> It does not ask. It waits.

### Beat 2c — The human (0:55–1:15) — 22 words

> One human. One decision.
> When I approve, the held supplier record is written into ERPNext.
> Nothing reached the ERP before that click.

The stand-in attachment is not narrated. The proof is the supplier row and
the click that preceded it.

### Beat 2d — Supplier two (1:15–1:45) — 30 words

> Supplier two is a separate case. The approval does not carry over.
> It reads clean, so nobody stops it.
> Its fields come from a text fixture. No OCR, no rekeying.

Cut to fit the recorded take (2026-08-25): supplier two onboards in 12.6 s
and its whole lifecycle plays in 34 s, so this beat holds only the
data-boundary sentence. The overlooked-user line moved to the end of 2f, over
the held end frame; "a year and a half in eighty seconds" was dropped (the
number was never true of the run).

### Beat 2e — Route two: the clock (1:45–2:05) — 38 words

> Next, the clock fires twice, with no agents involved.
> The first check is not due. Nothing happens.
> The second check is due. A real renewal email goes out.
> Nobody answers, so a purchasing hold lands in the ERP.

### Beat 2f — Route three: the certificate (2:05–2:25) — 48 words

> The renewed certificate arrives as a published event, and policy routes it to evidence only.
> Every field traces to a verbatim span.
> The purchasing hold is released.
> Renewal, hold, release. We call that station-keeping.
>
> Three hundred eighty simulated business days. Five under a real hold. Zero human touches.

### Beat 3 — Architecture and the security boundary (2:25–2:57) — 108 words

> Here is the architecture, in six boxes.
> Events come in, and the coordinator proposes a route.
> The decision belongs to a deterministic gate, which uses a versioned fleet catalog.
> Only the outbox reaches the ERP, and Ground Control can pause a case on the way.
>
> That boundary is the security model.
> The agents hold no ERP credential.
> One executor runs as a scoped ERP role that cannot delete.
> The case state lives in Firestore for months, not in the model.
>
> Three departments, each with its permitted agents and commands. That table is the fleet.
> On Google Cloud you can see the registered graph, the services, and one trace.

Every sentence here is backed by a committed proof: the credential boundary by
`spikes/frappe_scoped_executor/evidence.json` (role read back off the live
site: no delete, no role widening, no financial documents) and the
`forbidden_agent_tool_edges` core contract; the durable-state line by the
Firestore-owned case version and outbox. "Memory" is said once and means
durable case state; nothing implies the agents learn.

### Beat 4 — Failure, recovery, and safety (2:57–3:30) — 111 words

> Now the part most demos skip. Failure.
> A failed ERP command stays durable.
> When the destination is repaired, the unattended sweep re-drives that same command to done.
> The result is one record, with no duplicate.
>
> **SYNC (injected doc)**
> Someone planted an instruction in this document.
> A deterministic scan catches it first, so no AI agent ever reads it.
> Nothing was written to the ERP.
>
> **SYNC (red field)**
> Here, a worker count is schema-valid, but it has no source span in the document.
> It is rejected, retried once, and then parked for a human.
> Again, nothing was written to the ERP.
> Every refusal lands in Telemetry, meaning the OpenTelemetry traces.
> It is not perfect, but it is contained.

Three panels, not four: the replay-safety contract is real but a fourth panel
in 35 seconds cannot be read. It stays on `/proof`.

### Beat 5 — Close (3:30–3:45) — 38 words

> Kepler did not discover that the planets move. He showed that their motion obeys law.
> Launch a case once. It stays up for months, and policy corrects it, not you.
> Put your work in orbit.
>
> Keplaria dot com.

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
- Right top: the ERPNext supplier list, live, cropped to the data region,
  masthead out of frame (the trademark call, recorded in `video-audit.md`
  on 2026-08-25).
- Right bottom: the route strip — incoming event chip, the coordinator's
  proposed roster, the policy verdict. This strip is what makes the three
  routes legible: it visibly shows 2 agents / 0 agents / 1 agent across the
  segment. Shipped in the console (`f9a3233`); no dependency remains.

| Beat | On screen | Overlay text |
|---|---|---|
| 1 | Black card: "Onboarding ends when the ERP record is created." — "ends" strikes through, replaced by "begins". Title card: Keplaria. | "45.1 s of machine time vs 663.5 s by hand — a 20 steps walkthrough, 19 of which the run removes; author-timed, not practitioner-reviewed" |
| 2a | Packet event chip; route strip: proposed "evidence + compliance", verdict "APPROVED 2/2" | "Deployed on Google Cloud · unedited from here" |
| 2b | Compliance result "sanctions near-match"; case moves into Ground Control's parked column showing `create_supplier — HELD`, `attach_evidence — HELD` | "Parked in 11.9 s · ERP writes so far: 0" |
| 2c | Cursor reads the case, clicks Approve; ERP pane: supplier row appears | "22.4 s human approval · 0.1 s execution" |
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
