"""Prove that a human rejection withholds, and that a hold never waits for one.

spikes/hitl_release closed the approve path: a parked case, a signed-in
reviewer, a real ERP write. Two things it explicitly did not prove, in its own
`what_this_proves`: anything about a rejection, and anything about the
restrictive commands that bypass the band guard. This closes both, and
corrects the way the second one was written down.

WHAT THE PLAN GOT WRONG, AND WHY IT MATTERS
-------------------------------------------
The standing note said `apply_hold` on a rejected case "has never been watched
happen through the UI". It never can be. app.executor.runner refuses only
PERMISSIVE commands, and ingress/main.py drains the outbox unconditionally
after every successful engine invocation -- so a claimed `apply_hold` executes
on that automatic drain, seconds after the event, while the case is parking.
By the time a reviewer opens the page the hold is already applied in the ERP.
That is the documented design (see app/executor/runner.py's module docstring:
a held-because-overdue supplier is held now rather than waiting on the
approval that gates the permissive commands beside it), not a defect -- but a
proof written to "watch the hold execute on rejection" would have sat waiting
for an event that cannot occur, and the only way to make it pass would have
been to weaken the claim until it described something else.

So the claim this harness actually measures is the stronger one:

    the restrictive command had already executed BEFORE the human decided,
    and the permissive commands beside it were still refused AFTER they
    decided.

Both halves are timestamps in Firestore, not readings of intent.

TWO CASES, TWO REJECTIONS
-------------------------
Track A -- rejection withholds. A review-band supplier that is NOT in the ERP.
The case parks holding `create_supplier`. A human rejects it. The command must
stay PENDING at `refused_by_policy` and no supplier may appear in the ERP.
This is the half a rejection is normally assumed to be.

Track B -- rejection does not withhold a restriction. A review-band supplier
that IS already in the ERP, walked through onboarding -> renewal -> overdue on
a fresh case. The lifecycle advances even though every permissive command sits
refused, because park_case persists lifecycle state through
_claim_lifecycle_commands and only the EXECUTION is gated. At the overdue step
the case parks holding four commands: three permissive ones refused since
earlier events, and `apply_hold`, already DONE. The human then rejects, and
the three stay refused -- now stamped band=blocked / gate_band=review /
approval_id, which is what distinguishes "a person refused this" from "nobody
has decided yet". The unit suite covers both directions, but every one of
those tests hand-builds `policy.band` and calls claim_command directly, and
none of them exercises a REVIEW-band case at all (they set "clear" and let the
rejection do the work). This runs the real graph.

    uv run --env-file .env python spikes/hitl_reject/harness.py park
    <sign in to the review service, REJECT both cases>
    uv run --env-file .env python spikes/hitl_reject/harness.py verify
    uv run --env-file .env python spikes/hitl_reject/harness.py teardown

`teardown` releases track B's ERP hold. It is a separate mode on purpose: the
evidence must be captured against a genuinely held supplier, and the hold must
not be left on a record the demo uses.

Start the yente VM before `park`. A stopped yente parks every case via
SCREENING_UNAVAILABLE (0.30) against a 0.20 threshold, which looks identical
in `phase` but adjudicates nothing; `park` refuses to proceed if that is what
happened.
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

# Running this by path rather than `-m` puts spikes/hitl_reject/ on
# sys.path[0], not the repo root, so `import app` fails without this. Same fix
# as spikes/hitl_release, spikes/judge_run, spikes/lifecycle.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.cloud import pubsub_v1  # noqa: E402

from app.executor.frappe import clear_supplier_hold, frappe_client  # noqa: E402
from app.lifecycle import APPLY_HOLD, RESTRICTIVE  # noqa: E402
from app.state.approvals import REJECTED  # noqa: E402
from app.state.commands import DONE, PENDING  # noqa: E402
from app.state.firestore import CASES, OUTBOX, get_client  # noqa: E402

PROJECT = "keplaria"
TOPIC = "keplaria-events"

# Track A. Review band on the live index: 0.637, match=false -- above the
# policy's 0.50 floor for SUBTHRESHOLD_CANDIDATE, below yente's own 0.70
# auto-flag. Measured 2026-08-17, same session as hitl_release's baseline.
# Deliberately NOT the name hitl_release used: that supplier now exists in the
# ERP, and "the rejection created nothing" is only checkable against a name
# that was never there. If this one ever starts flagging, the other measured
# review-band name is "Comercializadora Verde Andes Pacifico SAS" (0.600).
WITHHELD_SUPPLIER = "Comercial Andes Verde"

# Track B. The supplier hitl_release created on 2026-08-17. Reused rather than
# replaced precisely BECAUSE it already exists: apply_hold against a missing
# Supplier fails for a boring reason that would look like the guard working,
# and onboarding a fresh one would need an approval first and leave another
# record behind on an ERP that already carries 16 orphans.
HELD_SUPPLIER = "Andes Verde Import Export SAS"

# Dates and fixture from spikes/judge_run's LIFECYCLE sequence, which measured
# these exact transitions against the deployed graph on 2026-08-17. Reused
# verbatim rather than re-derived: the renewal window and overdue grace come
# from lifecycle_timing(), and a date picked by eye lands on NOT_DUE.
TRACK_B_STEPS = [
    ("new_supplier_packet", "2026-01-05", "fixture:andes-verde-cert-2027", "active"),
    ("renewal_due", "2026-12-01", None, "renewal_requested"),
    ("evidence_overdue", "2027-01-15", None, "held"),
]

EVIDENCE = Path(__file__).resolve().parent / "evidence.json"
STATE = Path(__file__).resolve().parent / ".parked.json"

AWAITING = "awaiting_approval"


def log(msg: str) -> None:
    print(f"[reject] {msg}", flush=True)


def publish(case_id: str, supplier: str, event_type: str, effective: str,
            ref: str | None = None) -> str:
    event = {
        "event_id": f"evt-{uuid.uuid4().hex[:10]}",
        "case_id": case_id,
        "event_type": event_type,
        "supplier": supplier,
        "schema_version": 1,
        "effective_date": effective,
    }
    if ref:
        event["document_ref"] = ref
    pub = pubsub_v1.PublisherClient()
    pub.publish(pub.topic_path(PROJECT, TOPIC), json.dumps(event).encode()).result(timeout=60)
    return event["event_id"]


def service_url(name: str) -> str:
    return subprocess.run(
        [
            "gcloud", "run", "services", "describe", name,
            "--region=us-central1", f"--project={PROJECT}",
            "--format=value(status.url)",
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def settle(db, case_id: str, minimum_version: int, timeout: int = 300) -> dict:
    """Poll until the case leaves `processing` at or past `minimum_version`.

    Keyed on phase AND version, not either alone: claim_event bumps
    case_version and sets phase="processing" the moment an event is claimed,
    so a poll landing in that gap sees a fresh version with no verdict; and
    track B publishes three events in sequence, so a poll landing before the
    next claim sees the PREVIOUS event's settled phase and would march on
    against stale state. Same helper shape as spikes/judge_run.
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        snap = db.collection(CASES).document(case_id).get()
        last = (snap.to_dict() or {}) if snap.exists else {}
        phase = last.get("phase")
        if phase and phase != "processing" and int(last.get("case_version") or 0) >= minimum_version:
            return last
        time.sleep(5)
    raise AssertionError(f"case {case_id} did not settle within {timeout}s; last: {last}")


def outbox(db, case_id: str) -> dict:
    """The case's outbox keyed by action, for readable assertions."""
    return {
        (d.to_dict() or {}).get("action"): (d.to_dict() or {})
        for d in db.collection(CASES).document(case_id).collection(OUTBOX).stream()
    }


def supplier_record(name: str) -> dict | None:
    """The ERP Supplier row, or None when it does not exist.

    Read directly rather than through scripts/erp.py: that tool answers
    "does a watchlist entity appear anywhere", which is a different question
    and would report this supplier as a finding rather than as state.
    """
    with frappe_client() as client:
        response = client.get(
            f"/api/resource/Supplier/{name}",
            params={"fields": json.dumps(["name", "on_hold", "hold_type"])},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["data"]


def _screening_summary(case: dict) -> dict:
    screening = case.get("screening") or {}
    candidates = screening.get("candidates") or []
    top = max(candidates, key=lambda c: c.get("score") or 0.0, default=None)
    return {
        "reachable": screening.get("reachable"),
        "error": screening.get("error"),
        "candidate_count": len(candidates),
        "top_candidate": top,
    }


def _assert_parked_for_a_near_match(case: dict, label: str) -> dict:
    """A park is not automatically the park we wanted.

    A stopped yente parks every case via SCREENING_UNAVAILABLE (0.30), which
    clears the same 0.20 review threshold and is indistinguishable in `phase`
    -- but there is no candidate on screen, so the reviewer adjudicates
    nothing and the rejection proves only that an outage parks cases.
    """
    phase = case.get("phase")
    policy = case.get("policy") or {}
    screening = _screening_summary(case)
    fired = [f.get("id") for f in policy.get("factors_fired") or []]

    if phase != AWAITING:
        raise AssertionError(
            f"{label}: expected phase {AWAITING!r}, got {phase!r}. Nothing to "
            f"reject. Do not retune the policy to force a park -- pick a "
            f"supplier that earns the band (band={policy.get('band')})."
        )
    if not screening["reachable"]:
        raise AssertionError(
            f"{label}: parked because screening was UNREACHABLE, not because a "
            f"near-match needs a human. Start the yente VM and re-run. "
            f"error={screening['error']}"
        )
    if "SUBTHRESHOLD_CANDIDATE" not in fired:
        raise AssertionError(
            f"{label}: parked and screening reachable, but "
            f"SUBTHRESHOLD_CANDIDATE did not fire. factors={fired}"
        )
    return screening


def park() -> int:
    db = get_client()

    # Preflight the ERP before publishing anything. Both tracks assert about
    # ERP state, and each needs the OPPOSITE starting condition -- discovering
    # that after two parks and two browser trips would waste the human beat,
    # which is the expensive part of this run.
    withheld_before = supplier_record(WITHHELD_SUPPLIER)
    if withheld_before is not None:
        log(f"FAIL  {WITHHELD_SUPPLIER!r} already exists in the ERP.")
        log("      Track A proves a rejection created nothing, which is not")
        log("      checkable against a name that was already there. Pick an")
        log("      unused review-band name (see the module docstring).")
        return 1
    held_before = supplier_record(HELD_SUPPLIER)
    if held_before is None:
        log(f"FAIL  {HELD_SUPPLIER!r} is not in the ERP.")
        log("      apply_hold against a missing Supplier fails for a reason")
        log("      that has nothing to do with the band guard.")
        return 1
    if held_before.get("on_hold"):
        log(f"FAIL  {HELD_SUPPLIER!r} is ALREADY on hold.")
        log("      Run `teardown` (or clear it by hand) first -- a hold that")
        log("      was already there proves nothing about this run.")
        return 1
    log(f"preflight ok: {WITHHELD_SUPPLIER!r} absent, {HELD_SUPPLIER!r} present and unheld")

    # --- Track A -------------------------------------------------------
    # Published and settled BEFORE track B starts. Agent Runtime allows one
    # concurrent query per region; overlapping the two tracks would 429 and
    # look like a graph failure.
    case_a = f"REJ-A-{uuid.uuid4().hex[:6].upper()}"
    log(f"track A (withhold): {case_a}, supplier {WITHHELD_SUPPLIER!r}")
    # No document_ref: decide() returns AWAITING_EVIDENCE with a single
    # create_supplier command, which is the whole of what track A needs to
    # see refused. A certificate would add attach_evidence and prove nothing
    # further here.
    event_a = publish(case_a, WITHHELD_SUPPLIER, "new_supplier_packet", "2026-08-17")
    # Held in its own name, not the loop's `case`: track B reassigns that
    # below, and track A's parked version is what its approval must bind to.
    case_a_parked = settle(db, case_a, 1)
    screening_a = _assert_parked_for_a_near_match(case_a_parked, "track A")
    policy_a = case_a_parked.get("policy") or {}
    box_a = outbox(db, case_a)
    log(f"  parked: band={policy_a.get('band')} score={policy_a.get('score')} "
        f"commands={sorted(box_a)}")

    if (box_a.get("create_supplier") or {}).get("status") != PENDING:
        log(f"FAIL  track A: create_supplier is not PENDING: {box_a.get('create_supplier')}")
        return 1

    # --- Track B -------------------------------------------------------
    case_b = f"REJ-B-{uuid.uuid4().hex[:6].upper()}"
    log(f"track B (restriction): {case_b}, supplier {HELD_SUPPLIER!r}")
    beats = []
    for index, (event_type, effective, ref, expected_state) in enumerate(TRACK_B_STEPS, start=1):
        publish(case_b, HELD_SUPPLIER, event_type, effective, ref)
        case = settle(db, case_b, index)
        state = (case.get("lifecycle") or {}).get("state")
        beats.append({
            "step": index, "event_type": event_type, "effective_date": effective,
            "lifecycle_state": state, "expected_state": expected_state,
            "phase": case.get("phase"), "ok": state == expected_state,
        })
        log(f"  {index}. {event_type:22} state={state} phase={case.get('phase')}")
        if state != expected_state:
            log(f"FAIL  track B step {index}: expected lifecycle state "
                f"{expected_state!r}, got {state!r}. The sequence depends on "
                f"park_case persisting lifecycle state while the commands stay "
                f"refused; if that changed, this whole track is invalid.")
            return 1

    screening_b = _assert_parked_for_a_near_match(case, "track B")
    box_b = outbox(db, case_b)
    hold = box_b.get(APPLY_HOLD) or {}
    statuses = {action: command.get("status") for action, command in sorted(box_b.items())}
    log(f"  parked: commands={statuses}")

    # The finding this harness exists to record. The hold must ALREADY be done,
    # with no human anywhere near it.
    if hold.get("status") != DONE:
        log(f"FAIL  track B: {APPLY_HOLD} is {hold.get('status')!r}, expected {DONE!r}.")
        log("      The restrictive command should have executed on the ingress")
        log("      drain that followed the overdue event, without waiting for a")
        log("      decision. If it did not, either RESTRICTIVE changed or the")
        log("      drain no longer runs after a parking invocation.")
        return 1
    held_now = supplier_record(HELD_SUPPLIER)
    if not (held_now or {}).get("on_hold"):
        log(f"FAIL  track B: outbox says {APPLY_HOLD} is done, but the ERP record "
            f"is not on hold: {held_now}")
        return 1
    log(f"  {APPLY_HOLD} already executed at {hold.get('updated_at')} -- "
        f"ERP on_hold={held_now.get('on_hold')}, BEFORE any human decision")

    refused_now = sorted(a for a, c in box_b.items() if c.get("status") == PENDING)
    log(f"  still refused pending a decision: {refused_now}")

    STATE.write_text(json.dumps({
        "parked_at": datetime.now(timezone.utc).isoformat(),
        "track_a": {
            "case_id": case_a, "event_id": event_a, "supplier": WITHHELD_SUPPLIER,
            "policy": policy_a, "screening": screening_a,
            "parked_case_version": case_a_parked.get("case_version"),
        },
        "track_b": {
            "case_id": case_b, "supplier": HELD_SUPPLIER,
            "beats": beats, "screening": screening_b,
            "parked_case_version": case.get("case_version"),
            "hold_updated_at": str(hold.get("updated_at")),
            "erp_on_hold_before_decision": bool(held_now.get("on_hold")),
            "refused_at_park": refused_now,
        },
    }, indent=2, default=str) + "\n")

    review = service_url("keplaria-review")
    log("")
    log("PARKED. Now do the part that cannot be automated -- REJECT BOTH:")
    # /review/{case_id}, not /cases/{case_id}: console/review.py serves the
    # decidable case, console/public.py serves the read-only one. Guessing the
    # public path 404s -- it did on hitl_release's first real run.
    log(f"  1. {review}/review/{case_a}   <- track A, click Reject")
    log(f"  2. {review}/review/{case_b}   <- track B, click Reject")
    log("  3. uv run --env-file .env python spikes/hitl_reject/harness.py verify")
    log("")
    log("Do NOT approve either one. An approval here is not a smaller result,")
    log("it is a different experiment, and track A would write to the ERP.")
    return 0


def verify() -> int:
    if not STATE.exists():
        log(f"FAIL  no {STATE.name} -- run `park` first.")
        return 1
    parked = json.loads(STATE.read_text())
    db = get_client()

    a = parked["track_a"]
    b = parked["track_b"]
    case_a = (db.collection(CASES).document(a["case_id"]).get().to_dict() or {})
    case_b = (db.collection(CASES).document(b["case_id"]).get().to_dict() or {})
    approval_a = case_a.get("approval") or {}
    approval_b = case_b.get("approval") or {}
    box_a = outbox(db, a["case_id"])
    box_b = outbox(db, b["case_id"])

    create = box_a.get("create_supplier") or {}
    hold = box_b.get(APPLY_HOLD) or {}
    permissive_b = {
        action: command.get("status")
        for action, command in sorted(box_b.items())
        if action not in RESTRICTIVE
    }

    withheld_after = supplier_record(WITHHELD_SUPPLIER)
    held_after = supplier_record(HELD_SUPPLIER)

    # The one measurement that makes the restrictive claim a fact rather than
    # a reading of the code: the ERP hold was written before the human decided.
    # Both are Firestore server timestamps, so they share a clock; a DONE
    # command is skipped by every later drain, so `updated_at` still marks the
    # moment it succeeded.
    hold_at = hold.get("updated_at")
    decided_at = approval_b.get("committed_at")
    hold_preceded_decision = bool(hold_at and decided_at and hold_at < decided_at)

    checks = {
        # -- track A: a rejection withholds --------------------------------
        "a_decision_recorded": bool(approval_a.get("decision")),
        "a_decision_is_reject": approval_a.get("decision") == REJECTED,
        "a_decided_by_a_verified_reviewer": bool(approval_a.get("actor")),
        "a_bound_to_the_parked_version": (
            approval_a.get("case_version") == a.get("parked_case_version")
        ),
        "a_create_supplier_still_pending": create.get("status") == PENDING,
        "a_no_supplier_in_the_erp": withheld_after is None,
        # -- track B: a rejection does not withhold a restriction ----------
        "b_decision_is_reject": approval_b.get("decision") == REJECTED,
        "b_hold_executed": hold.get("status") == DONE,
        "b_hold_preceded_the_decision": hold_preceded_decision,
        "b_erp_supplier_on_hold": bool((held_after or {}).get("on_hold")),
        "b_permissive_commands_still_pending": all(
            status == PENDING for status in permissive_b.values()
        ),
    }
    result = "PASS" if all(checks.values()) else "FAIL"

    evidence = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "what_this_proves": (
            "Two review-band cases were parked by the deployed graph and "
            "REJECTED by a human signed in through IAP. On the first, the "
            "rejection withheld a permissive command: create_supplier stayed "
            "PENDING and no Supplier was ever created in the ERP. On the "
            "second, the restrictive command had already executed -- the ERP "
            "hold was written before the reviewer decided, on the ingress "
            "drain that followed the overdue event -- while the permissive "
            "commands beside it stayed refused after the rejection. The band "
            "guard is one-directional in both directions, live."
        ),
        "what_this_does_not_prove": (
            "Not a general claim about restrictive commands: apply_hold is "
            "currently the only member of app.lifecycle.RESTRICTIVE. A "
            "`blocked`-band case still claims nothing, so a newly-sanctioned "
            "supplier still cannot be held -- unchanged by this run. Nothing "
            "here exercises a stale rejection, or a rejection racing a later "
            "event."
        ),
        "correction_to_the_standing_note": (
            "The plan recorded this as watching apply_hold execute ON a "
            "rejected case through the UI. That cannot happen: "
            "ingress/main.py drains unconditionally after every engine "
            "invocation and the guard refuses only permissive commands, so a "
            "claimed hold executes while the case is still parking, before "
            "any reviewer sees it. Measured here, not inferred -- see "
            "b_hold_preceded_the_decision."
        ),
        "track_a_withheld": {
            "case_id": a["case_id"], "supplier": WITHHELD_SUPPLIER,
            "policy": a["policy"], "screening": a["screening"],
            "approval": approval_a, "commands": box_a,
            "erp_supplier_after": withheld_after,
            "final_phase": case_a.get("phase"),
            "final_case_version": case_a.get("case_version"),
        },
        "track_b_restriction": {
            "case_id": b["case_id"], "supplier": HELD_SUPPLIER,
            "beats": b["beats"], "screening": b["screening"],
            "approval": approval_b, "commands": box_b,
            "hold_updated_at": str(hold_at),
            "decision_committed_at": str(decided_at),
            "permissive_after_rejection": permissive_b,
            "erp_supplier_after": held_after,
            "final_phase": case_b.get("phase"),
            "final_case_version": case_b.get("case_version"),
        },
        "checks": checks,
        "teardown_required": (
            f"{HELD_SUPPLIER} is on hold in the live ERP. Run "
            f"`harness.py teardown` to release it."
        ),
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str) + "\n")

    for name, ok in checks.items():
        log(f"{'PASS' if ok else 'FAIL'}  {name}")
    log(f"track B permissive after rejection: {permissive_b}")
    log(f"evidence -> {EVIDENCE}")
    if result == "PASS":
        log("")
        log(f"NOW RUN TEARDOWN -- {HELD_SUPPLIER!r} is on hold in the live ERP.")
    return 0 if result == "PASS" else 1


def teardown() -> int:
    """Release track B's ERP hold, after the evidence has been captured.

    Separate from `verify` deliberately: the evidence must be read against a
    genuinely held supplier, and a verify that cleaned up after itself could
    never be re-run against its own claim.
    """
    if not EVIDENCE.exists():
        log(f"FAIL  no {EVIDENCE.name} -- capture the evidence before releasing")
        log("      the hold, or the run proves nothing.")
        return 1
    before = supplier_record(HELD_SUPPLIER)
    if not (before or {}).get("on_hold"):
        log(f"{HELD_SUPPLIER!r} is not on hold; nothing to release.")
        return 0
    with frappe_client() as client:
        clear_supplier_hold(client, HELD_SUPPLIER)
    after = supplier_record(HELD_SUPPLIER)
    if (after or {}).get("on_hold"):
        log(f"FAIL  hold still set after release: {after}")
        return 1
    log(f"released the hold on {HELD_SUPPLIER!r} -- on_hold={after.get('on_hold')}")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "park":
        return park()
    if mode == "verify":
        return verify()
    if mode == "teardown":
        return teardown()
    log("usage: harness.py park | verify | teardown")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
