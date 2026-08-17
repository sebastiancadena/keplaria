"""Rehearse the full judge run end to end and time every beat.

Run:
    uv run --env-file .env python spikes/judge_run/harness.py

It pauses once, in the middle, for a human to approve a case in the browser.
Everything else is machine time, and machine time is what the 2:10 live-run
budget actually has to fit into.

WHY THIS IS TWO CASES, NOT ONE
------------------------------
The written sequence reads as one case: park it, approve once, then publish
the lifecycle events. That is not achievable, and the reason is in
app.nodes.assess_risk: an event that brings no screening of its own carries
the STORED band forward rather than re-scoring. That is deliberate and
correct -- re-scoring from `screening=None` would fire no factors, land
`clear`, and let the passage of time launder a blocked supplier.

The consequence for a review-band case is that it re-parks on every
subsequent event. Measured on 2026-08-17 (case PROBE-5F84975F): a
review-band supplier parked at step 1, and a `renewal_due` at step 2 parked
again with the same SUBTHRESHOLD_CANDIDATE factor carried forward, even
though the lifecycle itself advanced to `renewal_requested`. An approval
does not help: it binds to one `case_version` and stops applying the moment
the next event advances the case, and it never rewrites the gate's stored
verdict. So a single review-band case needs five approvals, not one.

Hence two tracks, run back to back:

  A. HITL track -- a review-band supplier that parks for a genuine
     near-match, is approved once by a human, and releases into the ERP.
  B. Lifecycle track -- a CLEAN supplier that never parks, carrying the
     four-step renewal / hold / evidence / release sequence.

Both are real deployed runs. What this costs is that the approve-once beat
and the lifecycle beat are two different suppliers on screen, rather than
one supplier carried through the whole sequence.
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.cloud import pubsub_v1  # noqa: E402

from app.executor.runner import effective_band  # noqa: E402
from app.state.commands import DONE  # noqa: E402
from app.state.firestore import CASES, OUTBOX, get_client  # noqa: E402

PROJECT = "keplaria"
TOPIC = "keplaria-events"
BUDGET_SECONDS = 130  # the 2:10 live-run budget

# Review band on the live index: 0.672, match=false, against syn-co-008.
# See spikes/hitl_release/harness.py for how this was chosen and for the
# two backup names.
REVIEW_SUPPLIER = "Andes Verde Import Export SAS"

# Clean on the live index: top candidate 0.192, well under the 0.50 floor
# SUBTHRESHOLD_CANDIDATE needs, so this supplier never parks and the
# lifecycle sequence runs unattended. Measured 2026-08-17. A NEW name each
# time this is re-baselined: app.executor's create_supplier_if_absent does
# not update an existing record, so a name left behind by an earlier run
# reports later steps against a stale ERP row.
CLEAN_SUPPLIER = "Distribuidora Llanos Azules SAS"

# (event_type, effective_date, document_ref, expected)
LIFECYCLE = [
    ("new_supplier_packet", "2026-01-05", "fixture:andes-verde-cert-2027",
     {"state": "active", "cycle": 1}),
    ("renewal_due", "2026-10-01", None, {"state": "active", "reason": "NOT_DUE"}),
    ("renewal_due", "2026-12-01", None, {"state": "renewal_requested"}),
    ("evidence_overdue", "2027-01-15", None, {"state": "held"}),
    ("certificate_received", "2027-01-20", "fixture:andes-verde-cert-2028",
     {"state": "active", "cycle": 2}),
]

EVIDENCE = Path(__file__).resolve().parent / "evidence.json"


def log(msg: str) -> None:
    print(f"[judge] {msg}", flush=True)


def publish(case_id: str, supplier: str, event_type: str, effective: str,
            ref: str | None = None) -> None:
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


def settle(db, case_id: str, step: int, timeout: int = 300) -> dict:
    """Wait for the graph run triggered by event `step` to finish.

    phase != "processing" AND case_version >= step: claim_event bumps the
    version and sets phase="processing" before the graph runs, so version
    alone would return the previous step's verdict paired with the new
    version. Every terminal node moves phase off "processing".
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        snap = db.collection(CASES).document(case_id).get()
        last = (snap.to_dict() or {}) if snap.exists else {}
        if (last.get("phase") and last["phase"] != "processing"
                and int(last.get("case_version") or 0) >= step):
            return last
        time.sleep(2)
    raise AssertionError(f"{case_id} step {step} did not settle in {timeout}s: {last}")


def outbox(db, case_id: str) -> dict:
    return {
        d.id: d.to_dict()
        for d in db.collection(CASES).document(case_id).collection(OUTBOX).stream()
    }


def drained_outbox(db, case_id: str, timeout: int = 120) -> dict:
    """The outbox once the ingress has finished draining it.

    `settle` is NOT sufficient to read a command ledger, and the first run of
    this harness misreported because of it. settle waits on the case
    document's phase, which the GRAPH writes from inside the engine; the
    ingress drains the outbox afterwards, in the same request but after the
    engine call returns (see ingress/main.py). So a ledger read taken the
    instant settle returns catches the last step's commands still PENDING and
    reports a clean run as a half-executed one -- 2026-08-17 read `clear_hold`
    as pending when it was `done` seconds later.

    Earlier steps hid this: their commands had the whole of the next step to
    drain before anything read them. Only the final step is exposed, which is
    the worst possible place for it, because the final step is the hold
    release -- the demo's closing beat.

    Returns whatever it has at timeout rather than raising: a genuinely stuck
    command is a finding to record, not a reason to lose the run's evidence.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        cmds = outbox(db, case_id)
        if cmds and not any(c.get("status") == "pending" for c in cmds.values()):
            return cmds
        time.sleep(2)
    return outbox(db, case_id)


def service_url(name: str) -> str:
    return subprocess.run(
        ["gcloud", "run", "services", "describe", name, "--region=us-central1",
         f"--project={PROJECT}", "--format=value(status.url)"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def hitl_track(db, steps: list) -> tuple[str, float, float]:
    """Park a case, wait for a human to approve it, confirm the ERP write.

    Returns (case_id, machine_seconds, human_seconds). The human wait is
    reported separately and deliberately excluded from the budget total: the
    2:10 covers what the recording has to sit through, and a reviewer
    clicking Approve is the one beat whose duration is a directing choice
    rather than a system property.
    """
    case_id = f"JR-A-{uuid.uuid4().hex[:6].upper()}"
    log(f"track A (HITL): {case_id}, supplier {REVIEW_SUPPLIER!r}")

    t0 = time.time()
    publish(case_id, REVIEW_SUPPLIER, "new_supplier_packet", "2026-01-05",
            "fixture:andes-verde-cert-2027")
    case = settle(db, case_id, 1)
    park_s = time.time() - t0

    policy = case.get("policy") or {}
    life = case.get("lifecycle") or {}
    parked_ok = (
        case.get("phase") == "awaiting_approval"
        and policy.get("band") == "review"
        and (case.get("screening") or {}).get("reachable") is True
    )
    steps.append({
        "track": "A", "beat": "event -> parked for review", "seconds": round(park_s, 1),
        "ok": parked_ok, "phase": case.get("phase"), "band": policy.get("band"),
        "score": policy.get("score"),
        "factors": [f.get("id") for f in policy.get("factors_fired") or []],
        "lifecycle": {"state": life.get("state"), "cycle": life.get("cycle")},
        "commands_parked": sorted(
            c.get("action") for c in outbox(db, case_id).values()
        ),
    })
    log(f"  parked in {park_s:.1f}s -- band={policy.get('band')} "
        f"lifecycle={life.get('state')}/{life.get('cycle')} "
        f"parked={steps[-1]['commands_parked']}")
    if not parked_ok:
        raise AssertionError(f"track A did not park for a near-match: {steps[-1]}")

    review = service_url("keplaria-review")
    log("")
    log(f"  APPROVE NOW: {review}/review/{case_id}")
    log("  (waiting -- this is the only human beat)")
    h0 = time.time()
    while True:
        case = (db.collection(CASES).document(case_id).get().to_dict() or {})
        _, _, approval_id = effective_band(case)
        if approval_id:
            break
        if time.time() - h0 > 1800:
            raise AssertionError("no approval within 30 minutes")
        time.sleep(3)
    human_s = time.time() - h0

    t1 = time.time()
    # decide() drains synchronously, so by the time the approval is visible
    # the commands have already run. This measures the read-back, not a wait.
    cmds = drained_outbox(db, case_id)
    release_s = time.time() - t1
    executed = sorted(c.get("action") for c in cmds.values() if c.get("status") == DONE)
    released_ok = "create_supplier" in executed
    steps.append({
        "track": "A", "beat": "human approval -> ERP write", "seconds": round(release_s, 1),
        "ok": released_ok, "human_seconds": round(human_s, 1),
        "decision": (case.get("approval") or {}).get("decision"),
        "actor": (case.get("approval") or {}).get("actor"),
        "executed": executed,
    })
    log(f"  approved after {human_s:.0f}s of human time; executed {executed}")
    if not released_ok:
        raise AssertionError(f"approval did not release create_supplier: {steps[-1]}")

    return case_id, park_s + release_s, human_s


def lifecycle_track(db, steps: list) -> tuple[str, float]:
    """The four-beat station-keeping sequence on a supplier that never parks."""
    case_id = f"JR-B-{uuid.uuid4().hex[:6].upper()}"
    log(f"track B (lifecycle): {case_id}, supplier {CLEAN_SUPPLIER!r}")

    total = 0.0
    for index, (event_type, effective, ref, expected) in enumerate(LIFECYCLE, start=1):
        t0 = time.time()
        publish(case_id, CLEAN_SUPPLIER, event_type, effective, ref)
        case = settle(db, case_id, index)
        elapsed = time.time() - t0
        total += elapsed

        life = case.get("lifecycle") or {}
        ok = all(
            (life.get("last_reason") if key == "reason" else life.get(key)) == value
            for key, value in expected.items()
        )
        steps.append({
            "track": "B", "beat": f"{index}. {event_type}", "seconds": round(elapsed, 1),
            "ok": ok, "expected": expected, "phase": case.get("phase"),
            "lifecycle": {"state": life.get("state"), "cycle": life.get("cycle"),
                          "reason": life.get("last_reason")},
            "band": (case.get("policy") or {}).get("band"),
        })
        log(f"  {index}. {event_type:22} {elapsed:5.1f}s  "
            f"{life.get('state')}/{life.get('cycle')} {life.get('last_reason')} "
            f"{'OK' if ok else 'MISMATCH ' + json.dumps(expected)}")
        if not ok:
            raise AssertionError(f"track B step {index} mismatch: {steps[-1]}")

    cmds = drained_outbox(db, case_id)
    steps.append({
        "track": "B", "beat": "final command ledger", "seconds": 0.0,
        "ok": not any(c.get("status") == "pending" for c in cmds.values()),
        "commands": {k: {"status": v.get("status"), "cycle": v.get("cycle"),
                         "action": v.get("action"), "external_id": v.get("external_id")}
                     for k, v in cmds.items()},
    })
    return case_id, total


def main() -> int:
    db = get_client()
    steps: list = []
    failure = None
    started = datetime.now(timezone.utc)

    try:
        case_a, machine_a, human_s = hitl_track(db, steps)
        case_b, machine_b = lifecycle_track(db, steps)
    except AssertionError as exc:
        failure = str(exc)
        case_a = case_b = None
        machine_a = machine_b = human_s = 0.0
        log(f"FAILED: {exc}")

    machine_total = machine_a + machine_b
    result = "PASS" if failure is None else "FAIL"
    within = machine_total <= BUDGET_SECONDS

    evidence = {
        "captured_at": started.isoformat(),
        "result": result,
        "what_this_is": (
            "One full deployed rehearsal of the live judge run: a case parked "
            "for a near-match and released by a human approval, then the "
            "four-beat lifecycle sequence. Machine time only -- the human "
            "approval is timed but excluded from the budget total."
        ),
        "case_ids": {"hitl": case_a, "lifecycle": case_b},
        "suppliers": {"hitl": REVIEW_SUPPLIER, "lifecycle": CLEAN_SUPPLIER},
        "machine_seconds": {
            "hitl_track": round(machine_a, 1),
            "lifecycle_track": round(machine_b, 1),
            "total": round(machine_total, 1),
        },
        "human_seconds": round(human_s, 1),
        "budget_seconds": BUDGET_SECONDS,
        "within_budget": within,
        "steps": steps,
    }
    if failure:
        evidence["failure"] = failure
    EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str) + "\n")

    log("")
    log(f"machine time: track A {machine_a:.1f}s + track B {machine_b:.1f}s "
        f"= {machine_total:.1f}s")
    log(f"budget {BUDGET_SECONDS}s -> {'WITHIN' if within else 'OVER'} "
        f"by {abs(machine_total - BUDGET_SECONDS):.1f}s")
    log(f"human approval took {human_s:.0f}s (excluded from the total)")
    log(f"evidence -> {EVIDENCE}")
    return 0 if (failure is None and within) else 1


if __name__ == "__main__":
    raise SystemExit(main())
