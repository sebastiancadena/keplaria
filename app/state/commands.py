"""Every side effect begins as a Firestore command with a deterministic ID.

The executor claims a command transactionally, calls the destination, then
records the external ID. A command already marked done is never re-driven, so a
replayed event produces exactly one downstream write. A command left pending by
a dead process is re-claimable, and for most actions the destination call is
also idempotent by deterministic ID on the far side, so a redundant re-drive
of a still-pending command is harmless there too.

`request_renewal` is the exception: the ERP does not deduplicate outbound
mail, so nothing on the far side stops a second identical renewal notice from
actually sending (see app.executor.frappe.send_supplier_message). For that
action, this cycle-scoped ledger — never re-driving a command already marked
done — is the *only* guard against a duplicate send, not a second layer on
top of destination-side idempotency.

Retry is bounded, not endless. The executor gets MAX_EXECUTION_ATTEMPTS
tries at a command; the last failure parks it as DEAD with `died_at`, and a
DEAD command is never re-claimed and never re-driven. It is not
resurrectable by design — `command_id` is cycle-scoped, so the next
lifecycle cycle issues a fresh command and a dead one blocks nothing.

`execution_attempts` and `attempts` count different things and must not be
conflated: `attempts` is incremented by claim_command (the graph side, once
per claim), `execution_attempts` by record_failure (the executor side, once
per attempted ERP call).
"""

from __future__ import annotations

from dataclasses import dataclass

from google.cloud import firestore

from app.state.firestore import CASES, OUTBOX

PENDING = "pending"
DONE = "done"
FAILED = "failed"
DEAD = "dead"

# The executor gets this many attempts at a command before it is parked as
# DEAD. Deliberately NOT expressed in terms of `attempts`: that field counts
# graph-side claims from claim_command, which grows every time an event
# re-enters the graph and has nothing to do with how many times the ERP call
# was actually tried.
MAX_EXECUTION_ATTEMPTS = 5


def command_id(case_id: str, action: str, cycle: int) -> str:
    """Deterministic command ID, unique per case, action, and cycle.

    The cycle is part of the identity because the lifecycle repeats: a
    supplier renewed in cycle 2 issues the same `action` as in cycle 1, and
    without the discriminator the second claim would find cycle 1's record
    already `done` and silently skip the work.
    """
    return f"{case_id}:{action}:c{cycle}"


@dataclass(frozen=True)
class CommandClaim:
    """Outcome of a claim attempt.

    `acquired` False with status DONE means the work is already complete and the
    caller must not repeat it.
    """

    acquired: bool
    status: str
    external_id: str | None = None
    result: dict | None = None


def _ref(db: firestore.Client, case_id: str, action: str, cycle: int):
    return (
        db.collection(CASES)
        .document(case_id)
        .collection(OUTBOX)
        .document(command_id(case_id, action, cycle))
    )


def claim_command(
    db: firestore.Client, case_id: str, action: str, cycle: int, payload: dict
) -> CommandClaim:
    """Claim the command for execution, or refuse if it already completed."""
    ref = _ref(db, case_id, action, cycle)

    @firestore.transactional
    def _claim(txn: firestore.Transaction) -> CommandClaim:
        snap = ref.get(transaction=txn)
        if snap.exists:
            data = snap.to_dict() or {}
            if data.get("status") == DONE:
                return CommandClaim(
                    False, DONE, data.get("external_id"), data.get("result")
                )
            if data.get("status") == DEAD:
                # Refused, NOT reset to PENDING. The graph re-claims on every
                # event and a review-band case re-parks on every later event,
                # so resetting here would resurrect a command the executor
                # already gave up on and the cap would never hold.
                return CommandClaim(False, DEAD)
            txn.update(
                ref,
                {
                    "status": PENDING,
                    "attempts": int(data.get("attempts", 0)) + 1,
                    "payload": payload,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return CommandClaim(True, PENDING)

        txn.set(
            ref,
            {
                "command_id": command_id(case_id, action, cycle),
                "case_id": case_id,
                "action": action,
                "cycle": cycle,
                "payload": payload,
                "status": PENDING,
                "attempts": 1,
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return CommandClaim(True, PENDING)

    return _claim(db.transaction())


def record_success(
    db: firestore.Client,
    case_id: str,
    action: str,
    cycle: int,
    external_id: str,
    result: dict,
) -> None:
    """Mark the command done and store the destination's identifier."""
    _ref(db, case_id, action, cycle).update(
        {
            "status": DONE,
            "external_id": external_id,
            "result": result,
            "error": None,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )


def record_failure(
    db: firestore.Client, case_id: str, action: str, cycle: int, error: str
) -> str:
    """Record an execution failure, parking the command as DEAD at the cap.

    Transactional because it is a read-modify-write on a counter: two
    concurrent drains of the same case (an ingress push and a sweep, say)
    would otherwise both read the same `execution_attempts` and each write
    back the same incremented value, losing an attempt and letting a broken
    command retry past its cap.

    Returns the resulting status — FAILED while retries remain, DEAD once
    they are exhausted — so the caller can report it without a second read.
    """
    ref = _ref(db, case_id, action, cycle)

    @firestore.transactional
    def _record(txn: firestore.Transaction) -> str:
        snap = ref.get(transaction=txn)
        data = (snap.to_dict() or {}) if snap.exists else {}
        attempts = int(data.get("execution_attempts", 0)) + 1
        status = DEAD if attempts >= MAX_EXECUTION_ATTEMPTS else FAILED

        update = {
            "status": status,
            "error": error,
            "execution_attempts": attempts,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
        if status == DEAD:
            update["died_at"] = firestore.SERVER_TIMESTAMP

        txn.update(ref, update)
        return status

    return _record(db.transaction())


def get_command(
    db: firestore.Client, case_id: str, action: str, cycle: int
) -> dict | None:
    snap = _ref(db, case_id, action, cycle).get()
    return snap.to_dict() if snap.exists else None
