"""Drives the full station-keeping sequence and records what happened.

Evidence lands in this directory, never in a scratchpad: a scratchpad dies
with the session, and evidence that cannot be re-read later is not evidence.

Run:
    uv run --env-file .env python spikes/lifecycle/harness.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Running `uv run python spikes/lifecycle/harness.py` (rather than `-m` from
# pyproject.toml) puts spikes/lifecycle/ on sys.path[0], not the repo root,
# so `import app` fails without this. Same fix as spikes/thin_vertical and
# spikes/policy_gate.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.cloud import pubsub_v1  # noqa: E402

from app.state.firestore import CASES, get_client  # noqa: E402

PROJECT = "keplaria"
TOPIC = "keplaria-events"


def publish(event: dict) -> str:
    """Same publishing path spikes/thin_vertical/verify.py uses."""
    publisher = pubsub_v1.PublisherClient()
    future = publisher.publish(
        publisher.topic_path(PROJECT, TOPIC), json.dumps(event).encode()
    )
    return future.result(timeout=60)


def wait_for_settle(db, case_id: str, step: int, timeout: int = 180) -> dict:
    """Poll until the case has processed the event for `step`.

    Keyed on case_version, not on the lifecycle reason: claim_event bumps
    case_version exactly once per claimed event, so step N settles at version
    N. Polling the reason instead would be wrong — two consecutive steps can
    legitimately produce the same reason (two NOT_DUE refusals), and the
    second poll would return the first step's state immediately.

    case_version alone is not sufficient, though: app/state/firestore.py's
    claim_event bumps case_version and sets phase="processing" the moment
    the event is claimed, *before* the graph has run — the lifecycle write
    only lands later, when commit_commands (or quarantine_case / park_case)
    finishes. A poll landing in that gap sees the new case_version paired
    with the PREVIOUS step's lifecycle block, which is truthy and so passes
    the naive check while describing the wrong step entirely. Observed live
    on 2026-08-14: a clock event (no LLM call, so the whole window can be
    well under 5s) settled to the correct "held" state, but a poll landing
    mid-write still read the prior step's "renewal_requested". Every code
    path that ends the graph run overwrites `phase` away from "processing"
    (commit_commands's two branches -> "committed"/"no_action",
    quarantine_case -> "quarantined", park_case -> "awaiting_approval"), and
    in commit_commands the lifecycle write is issued before that phase
    write — so requiring phase != "processing" closes the gap.

    The engine allows one concurrent query, so the ingress is serialised and
    a step can take tens of seconds.
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        snap = db.collection(CASES).document(case_id).get()
        last = (snap.to_dict() or {}) if snap.exists else {}
        if (
            int(last.get("case_version") or 0) >= step
            and last.get("phase") != "processing"
            and (last.get("lifecycle") or {})
        ):
            return last
        time.sleep(5)
    raise AssertionError(
        f"step {step} did not settle within {timeout}s; last state: {last}"
    )


CASE_ID = f"DEMO-{uuid.uuid4().hex[:8]}"

# Deliberately NOT "Comercializadora Andes Verde SAS", even though that is
# the name the lifecycle document fixtures below were written around and the
# name most of this repo's mocked tests use as a generic fixture string.
# Live, it is a confirmed yente match: fixtures/watchlist/entities.ftm.json
# carries it as syn-co-001 (topics: sanction), and
# spikes/agent_runtime/evidence.json recorded a real screening run against
# it scoring 1.0/match=true. Under the live risk gate
# (policy/supplier_risk.v1.json), SANCTIONS_MATCH alone (weight 0.70) clears
# the 0.60 block threshold, so a run against that name would quarantine at
# step 1 instead of reaching "active" — a real screening outcome, not a
# defect in the code under test, but not what this sequence is trying to
# demonstrate either. This harness follows spikes/thin_vertical/verify.py's
# precedent instead: a supplier name with no token overlap against any
# watchlist entity or alias, so screening executes for real (proving yente
# reachability) and returns clear, letting the sequence exercise the
# lifecycle transitions it is actually testing.
#
# Changed again from "Distribuidora Textiles Occidente SAS" (still clean
# against the watchlist, kept as-is above for the record) after
# create_supplier_if_absent's own documented contract bit this harness: it
# "does not update an existing record" on a duplicate create, so the ERP
# Supplier record that earlier runs left behind (created before
# app.lifecycle threaded a synthetic email_id through CREATE_SUPPLIER's
# payload) would keep reporting request_renewal as `failed` forever, even
# after the fix, because create_supplier_if_absent only reconciles email_id
# on a fresh create, not a found-existing one. A brand-new name is what
# actually exercises the fix instead of re-observing a pre-fix artifact.
SUPPLIER = "Talleres Cerro Dorado SAS"

SEQUENCE = [
    ("new_supplier_packet", "2026-01-05", "fixture:andes-verde-cert-2027",
     {"state": "active", "cycle": 1}),
    ("renewal_due", "2026-10-01", None, {"state": "active", "reason": "NOT_DUE"}),
    ("renewal_due", "2026-12-01", None, {"state": "renewal_requested"}),
    ("evidence_overdue", "2027-01-15", None, {"state": "held"}),
    ("certificate_received", "2027-01-20", "fixture:andes-verde-cert-2028",
     {"state": "active", "cycle": 2}),
]


def _write_evidence(steps: list, result: str, failure: str | None) -> Path:
    evidence = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "case_id": CASE_ID,
        "supplier": SUPPLIER,
        "result": result,
        "steps": steps,
    }
    if failure:
        evidence["failure"] = failure
    path = Path(__file__).resolve().parent / "evidence.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    return path


def main() -> None:
    """Run the sequence, writing evidence.json whether it passes or not.

    A step's failure is caught here rather than left to crash the process
    uncaught: this repo's own rule is that gate evidence belongs in the
    repo, and an uncaught exception on step 3 of 5 leaves no evidence.json
    at all — worse, it leaves whatever evidence.json a PRIOR run wrote
    sitting there uncorrected, silently describing a run that isn't this
    one. `except Exception`, not just `AssertionError`: a step's own
    assertion is not the only way this can fail mid-sequence — a publish
    timeout, a transient gRPC error from wait_for_settle's polling, or
    anything else raised between steps must not skip _write_evidence either,
    or exactly the scenario this module's docstring warns about (evidence
    that cannot be re-read later is not evidence) happens by omission rather
    than by writing to a scratchpad. `steps` therefore always gets written,
    with `result: "FAIL"` and a `failure` field naming exactly what broke,
    so a partial run is still evidence of what happened rather than nothing
    — and never stale evidence of a previous run.
    """
    db = get_client()
    steps: list = []

    try:
        for index, (event_type, effective_date, ref, expected) in enumerate(SEQUENCE, 1):
            event = {
                "event_id": f"{CASE_ID}-{index}",
                "case_id": CASE_ID,
                "event_type": event_type,
                "department": "procurement",
                "supplier": SUPPLIER,
                "effective_date": effective_date,
            }
            if ref:
                event["document_ref"] = ref

            publish(event)
            case = wait_for_settle(db, CASE_ID, index)
            lifecycle = case.get("lifecycle") or {}
            outbox = {
                d.id: d.to_dict()
                for d in db.collection(CASES).document(CASE_ID).collection("outbox").stream()
            }
            steps.append({
                "step": index,
                "event_type": event_type,
                "department": "procurement",
                "effective_date": effective_date,
                "expected": expected,
                "phase": case.get("phase"),
                "routing": case.get("routing"),
                "lifecycle": lifecycle,
                "certificate": case.get("certificate"),
                "commands": {k: {"status": v.get("status"), "cycle": v.get("cycle"),
                                 "external_id": v.get("external_id")}
                             for k, v in outbox.items()},
            })
            for key, value in expected.items():
                actual = lifecycle.get("last_reason") if key == "reason" else lifecycle.get(key)
                assert actual == value, f"step {index}: {key} was {actual!r}, wanted {value!r}"
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        path = _write_evidence(steps, "FAIL", failure)
        print(f"FAIL — {failure}; {len(steps)} step(s) recorded, evidence at {path}")
        raise

    path = _write_evidence(steps, "PASS", None)
    print(f"PASS — {len(steps)} steps, evidence at {path}")


if __name__ == "__main__":
    main()
