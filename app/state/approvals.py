"""Firestore is authoritative for whether a human decision applies.

A human decision arrives from a separate service, over an unreliable network,
possibly twice, and possibly about a case that has moved on since they read
it. The transaction below is what makes "approved exactly once, against the
state the reviewer actually saw" true rather than merely likely — the same
job, and the same shape, as claim_event in app/state/firestore.py.

This module records a decision; it authorizes nothing on its own, and it
executes nothing. The gate's verdict in `policy` is never touched, so the
machine's own conclusion stays readable next to the human's.
app.executor.runner is what combines them, and draining the outbox is a
separate call the caller makes after this one returns committed — putting it
in here would hand ERP-write authority to the one function whose entire
argument is that it grants nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.cloud import firestore

from app.state.firestore import CASES

APPROVALS = "approvals"

APPROVED = "approved"
REJECTED = "rejected"
_DECISIONS = frozenset({APPROVED, REJECTED})

AWAITING = "awaiting_approval"


@dataclass(frozen=True)
class ApprovalResult:
    """Outcome of an approval commit.

    `reason` is empty on success, and otherwise one of `invalid_decision`,
    `unknown_case`, `duplicate_approval`, `not_awaiting`, `stale_approval`.
    """

    committed: bool
    case_version: int
    reason: str = ""


def commit_approval(
    db: firestore.Client,
    case_id: str,
    approval_id: str,
    expected_case_version: int,
    decision: str,
    actor: str,
) -> ApprovalResult:
    """Commit `approval_id` against `case_id`, or reject it with a reason.

    Rejects a replay of an already-committed `approval_id`, a decision about a
    case that is not parked, and a decision taken against a case version that
    has since advanced. Every rejection leaves the case untouched.
    """
    case_ref = db.collection(CASES).document(case_id)
    approval_ref = case_ref.collection(APPROVALS).document(approval_id)

    @firestore.transactional
    def _commit(txn: firestore.Transaction) -> ApprovalResult:
        # Every read precedes every write: Firestore requires it.
        approval_snap = approval_ref.get(transaction=txn)
        case_snap = case_ref.get(transaction=txn)
        case = case_snap.to_dict() or {}
        current_version = int(case.get("case_version", 0))

        if decision not in _DECISIONS:
            return ApprovalResult(False, current_version, "invalid_decision")

        if not case_snap.exists:
            return ApprovalResult(False, current_version, "unknown_case")

        # Before phase and version, both of which a successful first call
        # changes — checking either first would report a consequence of the
        # original approval working instead of the fact that this is a replay.
        if approval_snap.exists:
            return ApprovalResult(False, current_version, "duplicate_approval")

        if case.get("phase") != AWAITING:
            return ApprovalResult(False, current_version, "not_awaiting")

        if int(expected_case_version) != current_version:
            return ApprovalResult(False, current_version, "stale_approval")

        record = {
            "approval_id": approval_id,
            "decision": decision,
            "actor": actor,
            "case_version": current_version,
            "committed_at": firestore.SERVER_TIMESTAMP,
        }
        txn.set(approval_ref, record)
        # merge=True: this must not disturb policy, routing, screening or any
        # other block the graph wrote. updated_at is refreshed alongside
        # approval so the console's list_cases (ordered on this field) sees
        # a case the moment a decision lands on it, not only when the graph
        # next runs — and so a case document can never end up missing the
        # field just because the only write it has ever received is an
        # approval.
        txn.set(
            case_ref,
            {"approval": record, "updated_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )
        return ApprovalResult(True, current_version)

    return _commit(db.transaction())
