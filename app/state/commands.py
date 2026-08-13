"""Every side effect begins as a Firestore command with a deterministic ID.

The executor claims a command transactionally, calls the destination, then
records the external ID. A command already marked done is never re-driven, so a
replayed event produces exactly one downstream write. A command left pending by
a dead process is re-claimable, because the destination call is idempotent by
deterministic ID on the far side too.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.cloud import firestore

from app.state.firestore import CASES, OUTBOX

PENDING = "pending"
DONE = "done"
FAILED = "failed"


def command_id(case_id: str, action: str) -> str:
    """Deterministic command ID. Same case and action always collide."""
    return f"{case_id}:{action}"


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


def _ref(db: firestore.Client, case_id: str, action: str):
    return (
        db.collection(CASES)
        .document(case_id)
        .collection(OUTBOX)
        .document(command_id(case_id, action))
    )


def claim_command(
    db: firestore.Client, case_id: str, action: str, payload: dict
) -> CommandClaim:
    """Claim the command for execution, or refuse if it already completed."""
    ref = _ref(db, case_id, action)

    @firestore.transactional
    def _claim(txn: firestore.Transaction) -> CommandClaim:
        snap = ref.get(transaction=txn)
        if snap.exists:
            data = snap.to_dict() or {}
            if data.get("status") == DONE:
                return CommandClaim(
                    False, DONE, data.get("external_id"), data.get("result")
                )
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
                "command_id": command_id(case_id, action),
                "case_id": case_id,
                "action": action,
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
    db: firestore.Client, case_id: str, action: str, external_id: str, result: dict
) -> None:
    """Mark the command done and store the destination's identifier."""
    _ref(db, case_id, action).update(
        {
            "status": DONE,
            "external_id": external_id,
            "result": result,
            "error": None,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )


def record_failure(db: firestore.Client, case_id: str, action: str, error: str) -> None:
    """Mark the command failed, leaving it eligible for a later retry."""
    _ref(db, case_id, action).update(
        {
            "status": FAILED,
            "error": error,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )


def get_command(db: firestore.Client, case_id: str, action: str) -> dict | None:
    snap = _ref(db, case_id, action).get()
    return snap.to_dict() if snap.exists else None
