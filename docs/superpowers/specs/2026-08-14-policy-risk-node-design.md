# Deterministic policy and risk node — design

- **Date:** 2026-08-14 (plan day 3)
- **Closes:** R30 — screening does not gate the ERP write
- **Implements:** `strategy/architecture-contracts.md` flight plan step 5
- **Status:** approved design, pending implementation plan

## Problem

The Compliance agent screens a supplier against self-hosted yente, the result is
persisted onto the case document, and `app/nodes.py::queue_supplier` claims the
`create_supplier` command **regardless of what screening found**. A flagged
supplier is still onboarded.

This is recorded as R30, the most dangerous claim risk in the project: a
compliance product whose own evidence shows a sanctioned onboarding is worse
than a product with no evidence at all. It is currently mitigated by honesty
rather than by behaviour — the README, the `queue_supplier` docstring, and the
risk register all state plainly that screening is advisory in this slice.

This design replaces that mitigation with enforcement.

## Scope

**In scope:** the screening gate. A deterministic, pure-code node that computes
a risk score and a band from screening results, case state, and a versioned
policy fixture, and decides whether the ERP command may be queued.

**Out of scope, deliberately:**

- **Expiry / hold / release rules.** `architecture-contracts.md` lists these
  under the same policy node, but the execution plan schedules renewal, hold,
  and release as separate days 5–7 work against the ERP executor, and they
  depend on case-state machinery that does not exist yet. The fixture schema
  leaves room for them; today's fixture does not contain them, and no
  documentation will claim it does.
- **Evidence-grounded scoring.** Contracts specify the score is computed from
  grounded evidence *and* screening *and* case state. The Evidence agent is
  still a stub (`apply_route` records a permitted `evidence` selection under
  `pending_implementation` and proceeds), so grounded evidence is unavailable.
  Today's inputs are screening results, `event_type`, and `amount`. No copy may
  claim evidence-grounded scoring until the Evidence agent exists.
- **Live human-in-the-loop pause.** `RequestInput` is deliberately not in the
  graph; contracts forbid drawing a pause node until Ground Control reinstates
  one (day 8). The `review` band terminates in a parked case, not a live pause,
  and is described that way everywhere.
- **The pre-existing `certificate_received` oddity.** That event type routes to
  evidence only and then queues `create_supplier` for an already-known supplier.
  This design makes the behaviour visible by routing it through the gate; it
  does not change it.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Gate outcomes | Three bands: `clear` / `review` / `blocked` | `review` is the exact branch day-8 `RequestInput` replaces, with no graph rewrite |
| Unreachable screening | `review`, with reason `SCREENING_UNAVAILABLE` | Zero ERP writes either way, but an unreachable service is not evidence of sanctions. R29 means this fires most mornings before the yente VM is started; mapping it to `blocked` would pollute the quarantine record with cases that were never actually screened |
| Decision model | Named factors that sum to a score | Produces both a number for flight-plan step 6's rationale synthesis and a defensible "why" for the trace and dashboard |
| Enforcement points | Graph branch (primary) + executor refusal (backstop) | The executor is a genuinely separate authorization boundary — Cloud Run ingress under `keplaria-ingress@`, not the engine identity — and currently enforces nothing |
| Fixture expressiveness | Condition kinds are code; selection, params, and weights are data | Keeps the policy suppliable by an admitted BigQuery/MCP Toolbox adapter without inventing an expression language |

## Components

| Path | Status | Responsibility |
|---|---|---|
| `policy/supplier_risk.v1.json` | new | Versioned fixture: thresholds and factor list |
| `app/risk.py` | new | Pure, no I/O: `load_policy()`, `assess()`, `RiskVerdict` |
| `app/nodes.py::assess_risk` | new | The gate node |
| `app/nodes.py::park_case` | new | Terminal node for the `review` band |
| `app/agent.py` | modified | Rewired branches |
| `app/nodes.py::_record_outcome` | modified | Persists a `policy` block onto the case doc |
| `app/executor/runner.py` | modified | Refusal-only guard before draining |

## The policy fixture

```json
{
  "policy_id": "supplier_risk",
  "policy_version": 1,
  "thresholds": { "review": 0.20, "block": 0.60 },
  "factors": [
    { "id": "SANCTIONS_MATCH",        "weight": 0.70,
      "when": { "kind": "screening_match" } },
    { "id": "SUBTHRESHOLD_CANDIDATE", "weight": 0.25,
      "when": { "kind": "screening_candidate_above", "score": 0.50 } },
    { "id": "SCREENING_UNAVAILABLE",  "weight": 0.30,
      "when": { "kind": "screening_unreachable" } }
  ]
}
```

`app/risk.py` holds a registry mapping each `kind` to a predicate function. The
fixture selects kinds, supplies their parameters, and assigns weights. This is
the split that keeps the policy as data — a BigQuery adapter can later supply
rows of `id` / `weight` / `kind` / `params` against the same schema — without
requiring a DSL interpreter in the gate.

Fail-closed-to-review is **expressed in the fixture, not hardcoded**:
`SCREENING_UNAVAILABLE` carries weight 0.30, which sits above the `review`
threshold and below the `block` threshold, so an unreachable yente lands in
`review` through the ordinary scoring mechanism with no special case in
`assess()`.

### Scoring

`assess()` evaluates factors in fixture order, sums the weights of those that
fired, clamps the total to `[0, 1]`, and cuts the band:

- `score >= thresholds.block` → `blocked`
- `score >= thresholds.review` → `review`
- otherwise → `clear`

Two weight choices are load-bearing rather than arbitrary, and the implementation
must not drift from them without revisiting this section:

- `SANCTIONS_MATCH` (0.70) is at or above the `block` threshold, so a confirmed
  match blocks on its own, with no other factor required.
- `SUBTHRESHOLD_CANDIDATE` (0.25) is at or above the `review` threshold and
  below `block`, so a lone near-match parks the case for a human instead of
  passing. This is the demo-valuable case and the substance of contract test 2.

`screening_candidate_above` fires for **any** candidate scoring above its
parameter, including one that also matched — so a confirmed match fires both
factors. The double count is intentional and safe: factors only ever raise the
score, so an additional firing can never weaken a verdict.

### Verdict shape

```json
{
  "policy_id": "supplier_risk",
  "policy_version": 1,
  "score": 0.95,
  "band": "blocked",
  "factors_fired": [
    { "id": "SANCTIONS_MATCH",        "weight": 0.70, "value": "syn-co-001 @ 1.000" },
    { "id": "SUBTHRESHOLD_CANDIDATE", "weight": 0.25, "value": "syn-co-008 @ 0.526" }
  ],
  "reasons": []
}
```

`reasons` carries structured reason codes for outcomes that are not ordinary
factor hits — `POLICY_UNAVAILABLE`, `SCREENING_MALFORMED`.

`policy_version` is persisted with the verdict, so a case permanently records
which policy decided it even if the fixture later changes. Version pinning
across a fixture change is not solved here; recording the version honestly is.

## Data flow

```text
apply_route ─┬─ "screen"  → screen_supplier → assess_risk ─┬─ "clear"   → queue_supplier
             ├─ "skip"    → assess_risk ──────────────────┤  "review"  → park_case
             └─ "blocked" → quarantine_case                └─ "blocked" → quarantine_case
```

The `skip` branch — an event type that requires no compliance agent — is routed
through `assess_risk` rather than straight to `queue_supplier`. This buys the
invariant that **no ERP command is ever queued without a persisted policy
verdict**, and it is what allows the executor guard to treat a missing verdict
as an anomaly to refuse rather than a legitimate state it must tolerate. With no
screening in state, no screening factor fires, the score is 0, and the band is
`clear`.

Screening *absent* (never required for this event type) and screening
*unreachable* (attempted and failed) remain distinct: the
`screening_unreachable` condition fires only when the screening block exists and
`reachable == false`.

`quarantine_case` is now reachable from two places — a refused routing proposal
and a blocked risk verdict. It records the policy verdict alongside the routing
decision. Neither `quarantine_case` nor `park_case` claims a command; both
produce zero writes to the outbox.

`park_case` writes phase `awaiting_approval`, the term
`architecture-contracts.md` already reserves for a case parked pending a human
decision. Nothing describes it as a live pause while it is not one.

## Error handling

`assess()` is a **total function** — it never raises. A gate that throws would
fail the graph invocation, and under R31 (Agent Runtime allows one concurrent
query, with a 60s–600s push retry policy) a systematically failing gate becomes
retry pressure instead of a decision. Every problem instead produces a verdict
with a reason code, so an auditable outcome always reaches the case document.

This does not become bug-swallowing because **validation moves to load time**.
`load_policy()` rejects a fixture that references an unregistered condition
kind, carries a weight outside `[0, 1]`, or has thresholds out of order — so
`assess()` cannot encounter an unknown kind at runtime. The class of error that
would justify raising is made impossible instead of caught.

| Condition | Outcome |
|---|---|
| Fixture missing or malformed | `blocked`, reason `POLICY_UNAVAILABLE` |
| `screening` present but malformed | `blocked`, reason `SCREENING_MALFORMED` |
| `screening` present, `reachable == false` | `review`, factor `SCREENING_UNAVAILABLE` |
| `screening` absent (never required) | No screening factor fires |

Fixture loading is **lazy and cached**, not performed at module import. A
malformed fixture failing at import would present as the log-less "failed to
start and cannot serve traffic" error that has already cost this project hours
of debugging. Failing at decision time is fail-closed and diagnosable, and a
contract test asserts the shipped fixture parses so CI catches it well before a
deploy.

### Executor guard

On entry, `execute_pending_commands` reads the case document once. If
`policy.band != "clear"`, or the `policy` block is absent:

- do **not** execute the command
- do **not** call `record_failure` — a refusal is not a failure
- do **not** mark the command `DONE`
- leave it `PENDING` and return
  `{"status": "refused_by_policy", "band": ..., "policy_version": ...}`

The guard is **refusal-only**: it can stop a write, never authorize one. It
cannot upgrade a verdict.

Leaving commands `PENDING` is deliberate and forward-compatible. A `review` case
that a human later approves flips its verdict to `clear`, and the next drain
executes normally — that is the day-8 approval flow, and it requires no change
to this design. Refusal is deterministic, so a later drain refuses identically;
it creates no retry churn and no DLQ pressure.

**What the guard is actually worth.** In the happy path it never fires:
`quarantine_case` and `park_case` claim no commands, so only `clear` cases ever
have one to drain. It exists for anomalous paths — a duplicate-event redelivery
draining a command queued under older state, or a graph-wiring bug. It is a
backstop at a real authorization boundary, not a second independent check, and
the README will describe it that way rather than implying defence in depth the
system does not have.

## Replay and idempotency

`assess()` is pure and deterministic, so a replayed event recomputes an
identical verdict and overwrites the persisted block with the same value. No
compare-and-swap is required. This preserves the existing day-3 property that a
replayed event leaves `attempts=1` and `case_version=1`.

## Telemetry

The `assess_risk` span carries `keplaria.policy_version`, `keplaria.risk_score`,
`keplaria.risk_band`, and the **ids** of the factors that fired. The values that
triggered each factor go to Firestore only, never to the trace: the data
handling contract keeps entity-identifying values out of logs, and this keeps
the gate on the right side of it by construction rather than by accident.

## Testing

| # | Contract test |
|---|---|
| 1 | `syn-co-001` (match 1.000) → `blocked`, quarantined, zero outbox commands |
| 2 | `syn-co-008` decoy (0.526, sub-threshold) → `review`, parked, zero commands |
| 3 | Clean supplier → `clear`, command queued, ERP write executes |
| 4 | yente unreachable → `review` + `SCREENING_UNAVAILABLE`, zero commands |
| 5 | Executor refuses a non-`clear` case and leaves the command `PENDING` |
| 6 | `skip` branch (no screening) → `clear`, command queued |
| 7 | Shipped fixture parses; every factor kind is registered; thresholds ordered |
| 8 | Determinism — identical inputs produce identical verdicts |

Test 1 is the R30 regression test, and it directly reverses the day-3 evidence
run in which the sanctioned fixture entity `syn-co-001` was written to the ERP
under `"passed": true`.

Tests 1, 2, and 4 are also the substance of the overdue screening-honesty eval
cases (the day-1 eval gate, still `pending`). Building the gate first is what
lets those cases assert a band rather than merely asserting that a record was
written.

## Definition of done

1. All eight contract tests pass.
2. Evidence written to `spikes/policy_gate/evidence.json` — in the repo, never
   a scratchpad.
3. **The advisory language is retracted.** The `queue_supplier` docstring, the
   README limitation paragraph, and R30's mitigation row in
   `strategy/risk-register.md` all currently state that screening gates nothing.
   Landing the code without updating them leaves the repository asserting the
   opposite of the truth.
4. R30 moved from open to mitigated-by-behaviour in the risk register.

## Known follow-ups, not part of this change

- The sanctioned supplier record created by the day-3 evidence run still exists
  in ERPNext and should be deleted before any on-camera work.
- `certificate_received` queues `create_supplier` for an already-known supplier
  (see Scope).
- Expiry / hold / release rules extend this fixture during the days 5–7 renewal
  work.
