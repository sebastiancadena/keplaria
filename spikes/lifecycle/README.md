# Closed-loop lifecycle harness

Drives one supplier through the full station-keeping sequence — onboarding,
a not-yet-due renewal check, a renewal request, an overdue hold, and a
renewal that clears the hold — against the deployed system, and records what
happened as committed evidence.

## What it proves

`spikes/thin_vertical/verify.py` proves one event reaches the ERP.
`spikes/policy_gate/verify.py` proves the risk bands in isolation, against
mocked screening. This harness is the first thing in the repo that drives
the full `app/lifecycle.py` state machine end to end against the deployed
Agent Runtime graph and Cloud Run ingress: five real events, five real
Pub/Sub publishes, five real Firestore commits, culminating in a supplier
that has renewed once (cycle 1 -> cycle 2) and been held and released along
the way.

## Requirements

- `.env` populated per `.env.example` and passed with `--env-file .env` —
  the harness needs `GOOGLE_CLOUD_PROJECT`/ADC for Pub/Sub and
  `FIRESTORE_PROJECT_ID` for the Firestore client (see
  `app/state/firestore.py get_client()` for why those are two different
  variables).
- `gcloud auth application-default login` already done (ADC).
- The `keplaria-events` topic provisioned (`infra/events/setup.sh`) and the
  Agent Runtime graph + Cloud Run ingress deployed and current — see the
  README's "Deploying to Agent Runtime" section. This harness publishes
  through the same Pub/Sub path `spikes/thin_vertical/verify.py` uses
  (`pubsub_v1.PublisherClient().publish(...)`); it does not call the engine
  or the ingress directly.
- The yente screening VM reachable — with it down, `SCREENING_UNAVAILABLE`
  (weight 0.30) outscores the 0.20 review threshold and step 1 parks at
  `awaiting_approval` instead of reaching `active`, which fails the harness
  for a reason unrelated to the code under test.

## Why the supplier name differs from the repo's usual fixture string

Most of this repo's mocked tests use `"Comercializadora Andes Verde SAS"` as
a generic supplier fixture. This harness does not, on purpose: that name is
a confirmed live yente match (`syn-co-001` in
`fixtures/watchlist/entities.ftm.json`, scored 1.0/match=true in
`spikes/agent_runtime/evidence.json`), and under the live risk gate
(`policy/supplier_risk.v1.json`) `SANCTIONS_MATCH` alone clears the block
threshold. Run this harness against that name and step 1 quarantines
instead of reaching `active` — correct screening behaviour, not a bug, but
not what a closed-loop lifecycle demo is trying to show. `SUPPLIER` here is
`"Talleres Cerro Dorado SAS"`: no token overlap with any watchlist entity or
alias, following the same precedent `spikes/thin_vertical/verify.py` set for
its own supplier name.

The name has changed twice, both times for reasons worth knowing before
picking a third: an earlier value, `"Distribuidora Textiles Occidente
SAS"`, is also clean against the watchlist and is not itself the reason it
was replaced — it was replaced because `create_supplier_if_absent` "does
not update an existing record" on a duplicate create (see
`app/executor/frappe.py`), so the ERP Supplier record that earlier runs
left behind — created before a fix below existed — kept reporting
`request_renewal` as `failed` forever afterward, since a found-existing
record's `email_id` is never reconciled on a later run. Re-running against
a name this harness has used before is safe (see "Re-running"); picking a
new one is only needed to prove a fix that changes what a *fresh* create
does, the way validating the email_id fix below required one.

## Run

```bash
uv run --env-file .env python spikes/lifecycle/harness.py
```

Each step publishes one event and polls the case document in Firestore
until `case_version` reaches that step (see `wait_for_settle` — it does not
poll on the lifecycle reason, because two consecutive steps can legitimately
produce the same reason, e.g. two `NOT_DUE` refusals in a row). The engine
allows one concurrent query and 30/min, so the ingress is serialised and a
step can take tens of seconds; expect the full run to take a few minutes.

On a fully working system: `PASS — 5 steps, evidence at
.../spikes/lifecycle/evidence.json`. A failed assertion prints which step
and which field diverged from what was expected, and `evidence.json` still
gets written — with `result: "FAIL"` and a `failure` field — covering every
step that ran before the failing one, so a partial run is never lost the
way an uncaught traceback would lose it.

## History: three defects this harness found and that are now fixed

The first two committed evidence runs (2026-08-14) were `FAIL` at step 5,
reproduced across six live attempts. All three root causes below are now
fixed, deployed, and reflected in the currently-committed `PASS`
`evidence.json` — kept here because the fixes live in `app/`, not in this
directory, and a future change to any of them should re-read this history
first.

1. **The coordinator over-routed `certificate_received`.** `app/agent.py`'s
   `mission_coordinator` prompt said, explicitly, "certificate_received:
   ... Engage evidence only; do not re-screen unless entity fields changed" —
   and still proposed `[evidence, compliance]` for that event type on every
   observed live run (six for six, at `temperature=0`, including after a
   rewritten, more emphatic instruction that did not change the outcome).
   **Fix (user's decision, not a prompt change):** `app/policy.py`'s
   `validate_route` now drops a known-but-disallowed agent from the route
   instead of refusing the whole proposal — the guarantee it holds is still
   absolute (no agent runs unless policy permits it), it just no longer
   quarantines a legitimate business event over the coordinator's
   over-caution. `app/nodes.py`'s `apply_route` records what got dropped in
   a `dropped` field on the persisted routing decision, so the audit trail
   still shows the coordinator proposed `compliance` and policy removed it
   — see `evidence.json` step 5's `routing.dropped`.
2. **A Supplier was created with no `email_id`**, so `request_renewal`
   failed every time with `"supplier ... has no email_id to write to"` — by
   design (`app/executor/frappe.py send_supplier_message`: "a Supplier with
   no email_id is an error, not a silently skipped send"), but nothing
   upstream ever set one. **Fix:** `app/lifecycle.py`'s `CREATE_SUPPLIER`
   command payload now carries a synthetic, deterministic, RFC
   2606-reserved `@example.com` address (`_synthetic_email`, slugified from
   the supplier name), and `create_supplier_if_absent` sets it on the
   record when given one. Opt-in, not a new default inside
   `create_supplier_if_absent` itself, so a caller that wants the bare
   no-email path (e.g. `tests/integration/test_frappe_executor.py`'s
   `supplier_without_email` fixture) still gets it.
3. **A `certificate_received` event with no fresh screening scored `clear`
   instead of carrying the stored verdict forward** — a design defect, not
   an implementation slip: `assess_risk`'s carry-forward branch conditioned
   on "is this a clock event," which happened to coincide with "no
   screening" for every event type that existed when it was written, but
   stopped coinciding the moment `certificate_received` (agentic, not a
   clock event, but also never populating `screening` — its permitted route
   is `{evidence}` only) could reach the gate. Re-scoring that fresh from
   `screening=None` fires no factors, scores 0.0, and lands `clear` —
   laundering a previously blocked supplier via a mailed-in certificate.
   **Fix:** the condition is now `screening is None`, not event-type
   membership in `CLOCK_EVENTS`, covering every event that can reach the
   gate with nothing of its own to score, present or future.

The fix for #2 also means the `SUPPLIER` name changed a second time (see
above) — a duplicate `create_supplier` call never reconciles `email_id`
onto an already-existing record, so validating the fix needed a name this
harness had never used before.

## Re-running

Each run generates a fresh `CASE_ID` (`DEMO-<8 hex chars>`), so re-running
the harness never collides with a previous run's case document. It does
reuse the same `SUPPLIER` name every time, but that is safe, not just
tolerated: every executor write this sequence triggers
(`create_supplier_if_absent`, `attach_evidence`, `set_supplier_hold`) is
idempotent by construction on supplier name and cycle number, not on
`case_id` (see `app/executor/frappe.py`) — a repeat run against an
already-onboarded supplier just re-finds the same Supplier record and the
same `-c1`/`-c2` attachments (`created: False`) and proceeds. A genuinely
fresh case replaying the same lifecycle transitions against already-current
ERP state is exactly the redelivery scenario this design exists to survive.
No purge is required between runs; purge is for removing the demo data
afterwards (below).

## Cleanup

The harness creates one live ERPNext Supplier record plus its case document
and outbox in Firestore. To remove them after a demo or a re-run (substitute
whatever `SUPPLIER` was set to at the time — see "Why the supplier name
differs" above for why that has changed more than once):

```bash
uv run --env-file .env python scripts/erp.py purge --supplier "Talleres Cerro Dorado SAS" --yes
```

This is a human-triggered action by design — `scripts/erp.py` never deletes
without both an explicit target and `--yes`. Run
`uv run --env-file .env python scripts/erp.py audit` afterwards to confirm
the ERP and the live case store are both clean.
