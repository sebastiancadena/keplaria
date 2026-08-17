"""Park a case at `awaiting_approval`, then prove a human released it.

Until this ran, the approval path was proven by test and the reviewer
identity was proven in a browser (a real IAP sign-in), but no human decision
had ever released a real case into a real ERP write through the deployed
UI -- the queue happened to be empty at that sign-in.

Two phases, because the middle step is a human in a browser and cannot be
automated from here:

    uv run --env-file .env python spikes/hitl_release/harness.py park
    <sign in to the review service, approve the case>
    uv run --env-file .env python spikes/hitl_release/harness.py verify

`park` publishes one `new_supplier_packet` and waits for the case to settle
at `awaiting_approval`. `verify` reads what the human's decision actually
did and writes evidence.json. Evidence lands in this directory, never in a
scratchpad: a scratchpad dies with the session, and the day-2 HITL evidence
was already lost that way once.

Start the yente VM before running `park`. A stopped yente is not a neutral
absence -- it fires SCREENING_UNAVAILABLE (0.30) against a 0.20 review
threshold, so the case still parks, but for the wrong reason and with no
screening candidates to adjudicate. That parks a case that proves nothing.
`park` refuses to proceed if that is what happened.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Running this by path rather than `-m` puts spikes/hitl_release/ on
# sys.path[0], not the repo root, so `import app` fails without this. Same
# fix as spikes/lifecycle, spikes/thin_vertical and spikes/policy_gate.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.cloud import pubsub_v1  # noqa: E402

from app.state.approvals import APPROVED  # noqa: E402
from app.state.commands import DONE  # noqa: E402
from app.state.firestore import CASES, OUTBOX, get_client  # noqa: E402

PROJECT = "keplaria"
TOPIC = "keplaria-events"

# Chosen against the LIVE index on 2026-08-17, not from the eval dataset.
# tests/eval's `screening_decoy_review` case uses "Comercial Andes Pacifico
# SAS", whose review-band score comes from tests/eval/yente_stub.py. Against
# the real yente that name tops out at 0.459 -- below the 0.50 floor
# SUBTHRESHOLD_CANDIDATE needs -- so it scores 0.0 and lands CLEAR. It would
# have parked nothing.
#
# This name scores 0.672 with match=false against syn-co-008 ("Andes Verde
# Logistica S.A.S.", topics: sanction.linked): above the policy's 0.50 floor,
# below yente's own 0.70 auto-flag, which is exactly the band the factor
# exists for -- a near-match a human should adjudicate rather than a hit the
# machine should act on alone. Risk score 0.25, inside [0.20, 0.60) -> review.
#
# The live scoring gap here is narrow and worth knowing before you retune
# anything: dropping or altering a single token jumps straight from 0.459 to
# 0.750+/match=true. Names that do land in the band, measured the same day:
# "Comercial Andes Verde" 0.637, "Comercializadora Verde Andes Pacifico SAS"
# 0.600. If this name ever starts flagging, use one of those rather than
# inventing a new one untested.
SUPPLIER = "Andes Verde Import Export SAS"

EVIDENCE = Path(__file__).resolve().parent / "evidence.json"
STATE = Path(__file__).resolve().parent / ".parked.json"

AWAITING = "awaiting_approval"


def log(msg: str) -> None:
    print(f"[hitl] {msg}", flush=True)


def publish(event: dict) -> str:
    publisher = pubsub_v1.PublisherClient()
    future = publisher.publish(
        publisher.topic_path(PROJECT, TOPIC), json.dumps(event).encode()
    )
    return future.result(timeout=60)


def service_url(name: str) -> str:
    return subprocess.run(
        [
            "gcloud", "run", "services", "describe", name,
            "--region=us-central1", f"--project={PROJECT}",
            "--format=value(status.url)",
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def wait_for_park(db, case_id: str, timeout: int = 300) -> dict:
    """Poll until the case reaches a terminal phase for its first event.

    Keyed on phase, not on case_version alone: claim_event bumps
    case_version and sets phase="processing" the moment the event is
    claimed, before the graph has run, so a poll landing in that gap sees
    the new version paired with no verdict at all. Every path that ends a
    graph run moves phase off "processing" (park_case -> awaiting_approval,
    quarantine_case -> quarantined, commit_commands -> committed/no_action),
    so waiting for that is what closes the gap.

    Returns whatever it settled as -- including the wrong thing. Deciding
    whether `quarantined` or `committed` is acceptable is the caller's job,
    and it is not: see main().
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        snap = db.collection(CASES).document(case_id).get()
        last = (snap.to_dict() or {}) if snap.exists else {}
        phase = last.get("phase")
        if phase and phase != "processing" and int(last.get("case_version") or 0) >= 1:
            return last
        time.sleep(5)
    raise AssertionError(f"case did not settle within {timeout}s; last state: {last}")


def _screening_summary(case: dict) -> dict:
    screening = case.get("screening") or {}
    candidates = screening.get("candidates") or []
    top = max(candidates, key=lambda c: c.get("score") or 0.0, default=None)
    return {
        "reachable": screening.get("reachable"),
        "endpoint": screening.get("endpoint"),
        "error": screening.get("error"),
        "candidate_count": len(candidates),
        "top_candidate": top,
        "flagged": screening.get("flagged") or [],
    }


def park() -> int:
    case_id = f"HITL-{uuid.uuid4().hex[:8].upper()}"
    event = {
        "event_id": f"evt-{uuid.uuid4().hex[:10]}",
        "case_id": case_id,
        "event_type": "new_supplier_packet",
        "supplier": SUPPLIER,
        "schema_version": 1,
        "effective_date": "2026-08-17",
    }
    log(f"case {case_id}, supplier {SUPPLIER!r}")
    message_id = publish(event)
    log(f"published message {message_id}; waiting for the graph to settle")

    db = get_client()
    case = wait_for_park(db, case_id)
    phase = case.get("phase")
    policy = case.get("policy") or {}
    screening = _screening_summary(case)
    log(f"settled: phase={phase} band={policy.get('band')} score={policy.get('score')}")
    log(f"screening: {json.dumps(screening['top_candidate'])}")

    if phase != AWAITING:
        log(f"FAIL  expected phase {AWAITING!r}, got {phase!r}.")
        log("      Nothing to approve. Do not retune the policy to force a park --")
        log("      re-read the band above and pick a supplier that earns it.")
        return 1

    # A park is not automatically the park we wanted. A stopped yente parks
    # every case via SCREENING_UNAVAILABLE (0.30), which clears the same 0.20
    # review threshold and looks identical in `phase` -- but there is no
    # candidate on screen, so the human is adjudicating nothing and the run
    # proves only that an outage parks cases.
    if not screening["reachable"]:
        log("FAIL  the case parked because screening was UNREACHABLE, not because")
        log("      a near-match needs a human. Start the yente VM and re-run.")
        log(f"      error: {screening['error']}")
        return 1
    fired = [f.get("id") for f in policy.get("factors_fired") or []]
    if "SUBTHRESHOLD_CANDIDATE" not in fired:
        log("FAIL  parked, screening reachable, but SUBTHRESHOLD_CANDIDATE did not")
        log(f"      fire. Factors fired: {fired}")
        return 1

    STATE.write_text(json.dumps({
        "case_id": case_id,
        "event_id": event["event_id"],
        "message_id": message_id,
        "supplier": SUPPLIER,
        "parked_at": datetime.now(timezone.utc).isoformat(),
        "parked_case_version": case.get("case_version"),
        "policy": policy,
        "screening": screening,
    }, indent=2) + "\n")

    review = service_url("keplaria-review")
    log("")
    log("PARKED. Now do the part that cannot be automated:")
    # /review/{case_id}, NOT /cases/{case_id}: the two services do not share a
    # path scheme. console/public.py serves the read-only case at /cases/{id};
    # console/review.py serves the queue at /review and the decidable case at
    # /review/{id}. Guessing the public path here produced a 404 on the first
    # real run of this harness.
    log(f"  1. open  {review}/review/{case_id}")
    log(f"     (or the queue: {review}/review)")
    log("  2. sign in through IAP as the reviewer")
    log("  3. approve it, and read the result page's executed-commands list")
    log("  4. then run: uv run --env-file .env python spikes/hitl_release/harness.py verify")
    log("")
    log(f"public console: {service_url('keplaria-console')}/cases/{case_id}")
    return 0


def verify() -> int:
    if not STATE.exists():
        log(f"FAIL  no {STATE.name} -- run `park` first.")
        return 1
    parked = json.loads(STATE.read_text())
    case_id = parked["case_id"]

    db = get_client()
    snap = db.collection(CASES).document(case_id).get()
    case = (snap.to_dict() or {}) if snap.exists else {}
    approval = case.get("approval") or {}

    approvals = [
        d.to_dict()
        for d in db.collection(CASES).document(case_id).collection("approvals").stream()
    ]
    # OUTBOX, not "commands": app.state.commands._ref writes every command
    # into the case's outbox subcollection, keyed by command_id.
    commands = [
        d.to_dict()
        for d in db.collection(CASES).document(case_id).collection(OUTBOX).stream()
    ]

    decided = bool(approval.get("decision"))
    executed = [c for c in commands if c.get("status") == DONE]
    create = next((c for c in commands if c.get("action") == "create_supplier"), None)

    checks = {
        "case_parked_for_a_near_match": True,  # enforced by park(), recorded here
        "human_decision_recorded": decided,
        # APPROVED, not a hand-typed "approve": app.state.approvals only
        # accepts {"approved", "rejected"}, and the review form posts those
        # exact words. Hardcoding the verb form here failed a run whose
        # decision had in fact committed correctly.
        "decision_is_approve": approval.get("decision") == APPROVED,
        "decided_by_a_verified_reviewer": bool(approval.get("actor")),
        "approval_bound_to_the_parked_version": (
            approval.get("case_version") == parked.get("parked_case_version")
        ),
        "create_supplier_executed": bool(create and create.get("status") == DONE),
    }
    result = "PASS" if all(checks.values()) else "FAIL"

    evidence = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "what_this_proves": (
            "A case parked because a near-match needed adjudication, a human "
            "signed in through IAP and approved it, and that decision -- bound "
            "to the exact case version it was shown -- released the parked "
            "command into a real ERP write. Not proven here: anything about a "
            "rejection path, or about a decision made against a stale version."
        ),
        "case_id": case_id,
        "supplier": parked["supplier"],
        "parked": {
            "at": parked["parked_at"],
            "case_version": parked["parked_case_version"],
            "policy": parked["policy"],
            "screening": parked["screening"],
        },
        "approval": approval,
        "approvals_subcollection": approvals,
        "commands": commands,
        "checks": checks,
        "final_phase": case.get("phase"),
        "final_case_version": case.get("case_version"),
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str) + "\n")

    for name, ok in checks.items():
        log(f"{'PASS' if ok else 'FAIL'}  {name}")
    log(f"executed commands: {[c.get('action') for c in executed]}")
    log(f"evidence -> {EVIDENCE}")
    return 0 if result == "PASS" else 1


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "park":
        return park()
    if mode == "verify":
        return verify()
    log("usage: harness.py park | verify")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
