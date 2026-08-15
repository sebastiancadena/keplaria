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
`"Distribuidora Textiles Occidente SAS"`: no token overlap with any
watchlist entity or alias, following the same precedent
`spikes/thin_vertical/verify.py` set for its own supplier name.

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

## Known blockers (steps 1-4 pass; step 5 does not, as of 2026-08-14)

The evidence currently committed in this directory is a `FAIL` at step 5,
reproduced across six live runs. Two independent, pre-existing defects were
found this way — both outside this harness's own code, both something a
prior spike never exercised because none of them drove a full closed-loop
sequence against the deployed graph before this one:

1. **The coordinator over-routes `certificate_received`.** `app/agent.py`'s
   `mission_coordinator` prompt already says, explicitly, "certificate_received:
   ... Engage evidence only; do not re-screen unless entity fields changed" —
   and still proposes `[evidence, compliance]` for that event type on every
   observed live run (six for six, at `temperature=0`, including after a
   rewritten, more emphatic instruction that did not change the outcome).
   `app/policy.py`'s `ALLOWED_ROUTES` correctly rejects `compliance` for
   `certificate_received` and quarantines the case — the deterministic gate
   is doing exactly its job here, which is also why this fails safe rather
   than onboarding anything incorrectly. But it means step 5 (a
   `certificate_received` renewal) never reaches `app/lifecycle.py`'s
   `certificate_received` branch, so the case never leaves `held`.
2. **A Supplier is created with no `email_id`**, so `request_renewal`
   (step 3's `send_supplier_message`) fails every time with `"supplier ...
   has no email_id to write to"` — by design
   (`app/executor/frappe.py send_supplier_message`: "a Supplier with no
   email_id is an error, not a silently skipped send"), but nothing upstream
   of that call (`app/executor/frappe.py create_supplier_if_absent`'s
   payload) ever sets one. This means step 3's expected renewal
   Communication never appears in ERPNext regardless of the routing defect
   above — confirmed independently by reading the Supplier record directly
   (`email_id: ""`) and its outbox command (`status: "failed"`).

Neither was fixed as part of writing this harness: the first needs a
coordinator-behaviour decision (stronger prompting did not work; an actual
fix likely means restructuring the routing away from a free-text LLM
proposal, or accepting and living with the deterministic refusal), and the
second needs a decision about where a supplier's email address should come
from (nothing in `CanonicalEvent` or the certificate fixtures carries one
today). Both are documented in the committed `evidence.json`'s step 5
`routing` and `commands` blocks, not just here.

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
and outbox in Firestore. To remove them after a demo or a re-run:

```bash
uv run --env-file .env python scripts/erp.py purge --supplier "Distribuidora Textiles Occidente SAS" --yes
```

This is a human-triggered action by design — `scripts/erp.py` never deletes
without both an explicit target and `--yes`. Run
`uv run --env-file .env python scripts/erp.py audit` afterwards to confirm
the ERP and the live case store are both clean.
