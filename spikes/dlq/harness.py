"""Proves bounded retry and durable dead-lettering against deployed resources.

Run: uv run --env-file .env python spikes/dlq/harness.py

Writes spikes/dlq/evidence.json. Every check is a real observation of a real
resource — a command driven to `dead` through five genuine failures, and an
event that actually lands in dead_events — never a mocked or asserted-by-
construction result.

Uses a synthetic case ID prefixed DLQ- and a supplier name that is NOT on the
watchlist fixture, so it never creates a screening hit; and it targets a
deliberately invalid ERP action payload so the failures are real ERP refusals
rather than injected exceptions.

Ordering is deliberate. Check 4 is slow by nature (five deliveries at
60s-600s backoff is upwards of 20 minutes), so its poison event is published
FIRST and polled for LAST, with the four fast checks running while it backs
off. Any other order would make the run twice as long for no extra proof.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Running `uv run python spikes/dlq/harness.py` (rather than `-m` from
# pyproject.toml) puts spikes/dlq/ on sys.path[0], not the repo root, so
# `import app` fails without this. Same fix as the other spike harnesses.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import google.auth  # noqa: E402
import google.auth.transport.requests  # noqa: E402
import httpx  # noqa: E402
from google.cloud import pubsub_v1  # noqa: E402

from app.executor.frappe import create_supplier_if_absent, frappe_client  # noqa: E402
from app.executor.runner import execute_pending_commands  # noqa: E402
from app.lifecycle import APPLY_HOLD, CLEAR_HOLD  # noqa: E402
from app.state.commands import (  # noqa: E402
    DEAD,
    DONE,
    FAILED,
    MAX_EXECUTION_ATTEMPTS,
    claim_command,
    command_id,
    get_command,
)
from app.state.dead_events import DEAD_EVENTS  # noqa: E402
from app.state.firestore import CASES, get_client  # noqa: E402

PROJECT = "keplaria"
TOPIC = "keplaria-events"
INGRESS_URL = "https://keplaria-ingress-584548214478.us-central1.run.app"

# Neither name appears in fixtures/watchlist/entities.ftm.json, nor resembles
# one closely enough to score a candidate, so nothing here can produce a
# screening hit. Both are also absent from the ERP at the start of a run,
# which is what makes the hold calls fail for a real reason.
DEAD_SUPPLIER = "DLQ Dead Command Probe SAS"
SWEEP_SUPPLIER = "DLQ Sweep Probe SAS"

# Check 4's deadline. Five deliveries at the subscription's 60s-600s backoff
# is upwards of 20 minutes, and the dead-letter push adds another hop, so the
# deadline is generous on purpose. A timeout records what was observed rather
# than failing blind — see poll_for_dead_event.
DEAD_EVENT_DEADLINE_S = 2400
DEAD_EVENT_POLL_S = 15


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def publish(event: dict) -> str:
    """Same publishing path spikes/lifecycle/harness.py uses."""
    publisher = pubsub_v1.PublisherClient()
    future = publisher.publish(
        publisher.topic_path(PROJECT, TOPIC), json.dumps(event).encode()
    )
    return future.result(timeout=60)


def id_token() -> str:
    """An OIDC token for the deployed ingress.

    Cloud Run IAM is the only authorization on /admin/sweep, so this is the
    same mechanism the keplaria-command-sweep Cloud Scheduler job uses; it
    differs only in the identity presented. The scheduler's own identity
    (keplaria-sweeper@) is exercised separately by triggering the job.
    """
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    token = getattr(creds, "id_token", None)
    if not token:
        raise RuntimeError(
            "the ambient credential carries no OIDC id_token; Cloud Run IAM "
            "cannot authorize /admin/sweep without one"
        )
    return token


def seed_case(db, case_id: str, band: str) -> None:
    """Write the minimum case document the drain reads.

    `policy.band` is what app.executor.runner._policy_band reads to decide
    whether a PERMISSIVE command may execute. It is set explicitly rather
    than produced by a graph run because this harness is testing the retry
    ledger, not the gate — and running the graph would spend engine quota and
    screen a supplier for no reason.
    """
    db.collection(CASES).document(case_id).set(
        {
            "case_id": case_id,
            "case_version": 1,
            "phase": "processing",
            "supplier": DEAD_SUPPLIER if band != "clear" else SWEEP_SUPPLIER,
            "policy": {"band": band, "policy_version": 2},
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "harness": "spikes/dlq/harness.py",
        }
    )


# --------------------------------------------------------------------------
# Check 1 + 2 + 5: the command ledger's cap
# --------------------------------------------------------------------------


def check_command_reaches_dead(db, case_id: str) -> tuple[dict, dict]:
    """Drive a command whose ERP call cannot succeed until it parks as DEAD.

    Uses APPLY_HOLD against a supplier that does not exist in the ERP. That
    is a real ERP refusal (Frappe answers the PUT with a 4xx, which
    set_supplier_hold turns into a FrappeError), not an injected exception,
    and it creates nothing on the ERP side. APPLY_HOLD is also RESTRICTIVE,
    so the drain's band guard never refuses it — every one of these five
    attempts genuinely reaches Frappe and genuinely fails.
    """
    seed_case(db, case_id, band="review")
    claim = claim_command(
        db, case_id, APPLY_HOLD, 1, {"supplier_name": DEAD_SUPPLIER, "hold_type": "All"}
    )

    drains = []
    for i in range(MAX_EXECUTION_ATTEMPTS):
        results = execute_pending_commands(db, case_id)
        mine = [r for r in results if r.get("action") == APPLY_HOLD]
        drains.append(
            {
                "drain": i + 1,
                "status": mine[0]["status"] if mine else None,
                "error": (mine[0].get("error") or "")[:200] if mine else None,
            }
        )

    command = get_command(db, case_id, APPLY_HOLD, 1) or {}
    observed = {
        "case_id": case_id,
        "command_id": command_id(case_id, APPLY_HOLD, 1),
        "claim_acquired": claim.acquired,
        "drains": drains,
        "status": command.get("status"),
        "execution_attempts": command.get("execution_attempts"),
        "died_at": str(command.get("died_at")) if command.get("died_at") else None,
        "error": (command.get("error") or "")[:300],
        # `attempts` counts graph-side claims, `execution_attempts` counts
        # executor attempts. Both are recorded so the distinction the design
        # rests on is visible in the evidence rather than only in prose.
        "attempts": command.get("attempts"),
    }
    passed = (
        command.get("status") == DEAD
        and command.get("execution_attempts") == MAX_EXECUTION_ATTEMPTS
        and command.get("died_at") is not None
    )
    return {"pass": passed, "observed": observed}, command


def check_dead_command_not_re_driven(db, case_id: str, before: dict) -> dict:
    """A sixth drain must not touch the ERP or the document.

    The ERP call is not observed directly (there is no hook to observe it
    from here). It is observed by its only possible trace: a re-drive would
    have to call record_failure, which increments execution_attempts and
    rewrites updated_at inside a transaction. Both being unchanged after a
    full drain is proof the destination was never called.
    """
    updated_before = before.get("updated_at")
    results = execute_pending_commands(db, case_id)
    after = get_command(db, case_id, APPLY_HOLD, 1) or {}
    mine = [r for r in results if r.get("action") == APPLY_HOLD]

    observed = {
        "sixth_drain_result": mine[0] if mine else None,
        "execution_attempts_before": before.get("execution_attempts"),
        "execution_attempts_after": after.get("execution_attempts"),
        "updated_at_before": str(updated_before),
        "updated_at_after": str(after.get("updated_at")),
        "status_after": after.get("status"),
    }
    passed = (
        after.get("status") == DEAD
        and after.get("execution_attempts") == before.get("execution_attempts")
        and after.get("updated_at") == updated_before
        # Reported, not vanished: a dead command dropped from the drain
        # report would be indistinguishable from a case with no work.
        and bool(mine)
        and mine[0].get("status") == DEAD
    )
    return {"pass": passed, "observed": observed}


def check_new_cycle_unblocked(db, case_id: str) -> dict:
    """A dead command at cycle 1 must not block cycle 2.

    command_id is cycle-scoped, so this is the property that makes
    "not resurrectable" acceptable rather than a permanent stall.
    """
    claim = claim_command(
        db, case_id, APPLY_HOLD, 2, {"supplier_name": DEAD_SUPPLIER, "hold_type": "All"}
    )
    cycle1 = get_command(db, case_id, APPLY_HOLD, 1) or {}
    cycle2 = get_command(db, case_id, APPLY_HOLD, 2) or {}
    observed = {
        "cycle2_claim_acquired": claim.acquired,
        "cycle2_claim_status": claim.status,
        "cycle2_command_id": command_id(case_id, APPLY_HOLD, 2),
        "cycle1_status_unchanged": cycle1.get("status"),
        "cycle2_status": cycle2.get("status"),
    }
    passed = (
        claim.acquired is True
        and cycle2.get("status") == "pending"
        # The dead cycle-1 command must still be dead: claim_command refuses
        # a DEAD command rather than resetting it to PENDING, and that is the
        # half of the design that would silently invert the cap.
        and cycle1.get("status") == DEAD
    )
    return {"pass": passed, "observed": observed}


# --------------------------------------------------------------------------
# Check 3: the deployed sweep
# --------------------------------------------------------------------------


def check_sweep_finds_and_drives(db, case_id: str) -> dict:
    """A genuinely-failed command, repaired, then re-driven by the deployed sweep.

    The failure is real: CLEAR_HOLD against a supplier that does not exist in
    the ERP is refused by Frappe. The repair is real too — the supplier is
    then created, which is exactly the transient-destination-problem the
    sweep exists for. Nothing about the command is edited by hand; the sweep
    discovers it by its own collection-group query and drives it to done.
    """
    seed_case(db, case_id, band="clear")
    claim_command(db, case_id, CLEAR_HOLD, 1, {"supplier_name": SWEEP_SUPPLIER})

    first = execute_pending_commands(db, case_id)
    failed_now = get_command(db, case_id, CLEAR_HOLD, 1) or {}

    # Out-of-band repair of the destination, standing in for the human who
    # fixes whatever was broken. The sweep is what notices.
    with frappe_client() as client:
        created = create_supplier_if_absent(
            client,
            SWEEP_SUPPLIER,
            email_id="dlq-sweep-probe@example.com",
        )

    response = httpx.post(
        f"{INGRESS_URL}/admin/sweep",
        headers={"Authorization": f"Bearer {id_token()}"},
        json={},
        timeout=300,
    )
    summary = response.json() if response.status_code == 200 else {"error": response.text[:300]}
    after = get_command(db, case_id, CLEAR_HOLD, 1) or {}

    observed = {
        "case_id": case_id,
        "first_drain": [r for r in first if r.get("action") == CLEAR_HOLD],
        "status_after_first_drain": failed_now.get("status"),
        "execution_attempts_after_first_drain": failed_now.get("execution_attempts"),
        "supplier_created_out_of_band": created,
        "sweep_http_status": response.status_code,
        "sweep_summary": summary,
        "status_after_sweep": after.get("status"),
        "external_id": after.get("external_id"),
    }
    passed = (
        failed_now.get("status") == FAILED
        and response.status_code == 200
        and case_id in (summary.get("case_ids") or [])
        and after.get("status") == DONE
    )
    return {"pass": passed, "observed": observed}


# --------------------------------------------------------------------------
# Check 4: Pub/Sub dead-lettering
# --------------------------------------------------------------------------


def publish_poison_event() -> dict:
    """Publish an event the ingress cannot help but reject, every time.

    The case_id contains a "/", which is a valid string for CanonicalEvent
    (case_id is an unconstrained str) but not a valid Firestore document id:
    claim_event's `db.collection(CASES).document(case_id)` raises ValueError
    before anything is written, before the engine is invoked, and before the
    ERP is touched. The ingress therefore 500s on every delivery, costs no
    engine quota, and leaves no state behind — which is exactly what is
    wanted from a poison message whose only job is to exhaust redelivery.

    A malformed payload would NOT work: ingress/main.py acks unparseable
    events with 200 on purpose, because redelivery cannot fix them.
    """
    event_id = f"DLQ-POISON-{uuid.uuid4().hex[:8].upper()}"
    event = {
        "event_id": event_id,
        "case_id": "DLQ-POISON/UNWRITABLE",
        "event_type": "supplier_onboarding_requested",
        "supplier": DEAD_SUPPLIER,
        "schema_version": 1,
    }
    message_id = publish(event)
    return {"event_id": event_id, "message_id": message_id, "published_at": now(), "event": event}


def poll_for_dead_event(db, poison: dict, started: float) -> dict:
    """Wait for the dead-letter push to record the event.

    On timeout this records the elapsed time and whatever was observed
    instead of raising: a partial observation is evidence, a removed check is
    not.
    """
    ref = db.collection(DEAD_EVENTS).document(poison["event_id"])
    doc = None
    while time.monotonic() - started < DEAD_EVENT_DEADLINE_S:
        snap = ref.get()
        if snap.exists:
            doc = snap.to_dict() or {}
            break
        time.sleep(DEAD_EVENT_POLL_S)

    elapsed = round(time.monotonic() - started, 1)
    observed = {
        "event_id": poison["event_id"],
        "pubsub_message_id": poison["message_id"],
        "published_at": poison["published_at"],
        "elapsed_seconds": elapsed,
        "deadline_seconds": DEAD_EVENT_DEADLINE_S,
        "timed_out": doc is None,
        "recorded": doc is not None,
        "delivery_attempt": doc.get("delivery_attempt") if doc else None,
        "case_id": doc.get("case_id") if doc else None,
        "first_seen": str(doc.get("first_seen")) if doc else None,
        "last_seen": str(doc.get("last_seen")) if doc else None,
        # The single most valuable field in this file. delivery_attempt must
        # come from the CloudPubSubDeadLetterSourceDeliveryCount message
        # ATTRIBUTE, not the envelope's deliveryAttempt field: Pub/Sub
        # populates that field only on subscriptions that themselves carry a
        # dead-letter policy, and keplaria-events-dead-push deliberately has
        # none. A recorded 0 here means the handler read the wrong source and
        # is wrong in production, whatever the unit tests say.
        "delivery_attempt_source": (
            "CloudPubSubDeadLetterSourceDeliveryCount attribute"
            if doc and int(doc.get("delivery_attempt") or 0) >= 1
            else "unset or read from the wrong source"
        ),
    }
    passed = (
        doc is not None
        and int(doc.get("delivery_attempt") or 0) >= 1
        and int(doc.get("delivery_attempt") or 0) >= MAX_EXECUTION_ATTEMPTS
    )
    return {"pass": passed, "observed": observed}


# --------------------------------------------------------------------------


def main() -> int:
    db = get_client()
    suffix = uuid.uuid4().hex[:8].upper()
    dead_case = f"DLQ-DEAD-{suffix}"
    sweep_case = f"DLQ-SWEEP-{suffix}"

    # Published first so its 60s-600s backoff runs concurrently with the four
    # fast checks below rather than after them.
    poison = publish_poison_event()
    poison_started = time.monotonic()
    print(f"published poison event {poison['event_id']} (msg {poison['message_id']})")

    checks: dict[str, dict] = {}

    print("check 1: driving a command to dead through five real ERP refusals...")
    checks["command_reaches_dead"], dead_command = check_command_reaches_dead(db, dead_case)

    print("check 2: sixth drain must not re-drive it...")
    checks["dead_command_not_re_driven"] = check_dead_command_not_re_driven(
        db, dead_case, dead_command
    )

    print("check 3: deployed sweep must find and drive a stuck case...")
    checks["sweep_finds_and_drives"] = check_sweep_finds_and_drives(db, sweep_case)

    print("check 5: a new cycle must still be claimable...")
    checks["new_cycle_unblocked"] = check_new_cycle_unblocked(db, dead_case)

    print(
        f"check 4: waiting up to {DEAD_EVENT_DEADLINE_S}s for the dead-letter "
        f"record (five deliveries at 60s-600s backoff)..."
    )
    checks["dead_letter_records_event"] = poll_for_dead_event(db, poison, poison_started)

    result = "PASS" if all(c["pass"] for c in checks.values()) else "FAIL"
    evidence = {
        "captured_at": now(),
        "result": result,
        "what_this_proves": (
            "Retry is bounded and dead-lettering is durable, observed against "
            "deployed resources. A command whose ERP call cannot succeed was "
            "driven to the terminal `dead` state by five real Frappe refusals "
            "and was not re-driven a sixth time; the deployed POST /admin/sweep "
            "found a stuck case by its own collection-group query and drove its "
            "command to done; an event the ingress rejected on every delivery "
            "was recorded in dead_events with the delivery count Pub/Sub "
            "actually reported. Not proven here: that the sweep diagnoses a "
            "persistently broken destination (it does not), that a dead command "
            "can be resurrected (it cannot, by design), or anything about the "
            "Cloud Scheduler trigger's own 15-minute cadence beyond the job "
            "existing and being ENABLED."
        ),
        "environment": {
            "firestore_database": os.environ.get("FIRESTORE_DATABASE"),
            "ingress_url": INGRESS_URL,
            "topic": TOPIC,
            "max_execution_attempts": MAX_EXECUTION_ATTEMPTS,
        },
        "case_ids": {"dead": dead_case, "sweep": sweep_case},
        "suppliers": {"dead_probe": DEAD_SUPPLIER, "sweep_probe": SWEEP_SUPPLIER},
        "checks": {name: c["pass"] for name, c in checks.items()},
        "observations": {name: c["observed"] for name, c in checks.items()},
    }

    out = Path(__file__).with_name("evidence.json")
    out.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(f"\nwrote {out}")
    for name, c in checks.items():
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {name}")
    print(f"\n{result}")
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
