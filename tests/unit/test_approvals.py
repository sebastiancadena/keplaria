"""Unit tests for the approval commit transaction.

A human decision arrives from a separate service, over an unreliable network,
possibly twice, possibly about a case that has since moved on. The transaction
is the only thing that makes "approved exactly once, against the state the
human actually saw" true rather than merely likely.
"""

from __future__ import annotations

from app.state.approvals import (
    APPROVALS,
    APPROVED,
    REJECTED,
    commit_approval,
)
from app.state.firestore import CASES


def _parked(db, case_id: str, version: int = 3) -> None:
    """A case in exactly the state park_case leaves behind."""
    db.collection(CASES).document(case_id).set({
        "case_id": case_id,
        "case_version": version,
        "phase": "awaiting_approval",
        "policy": {"band": "review", "policy_version": 2},
    })


def test_an_approval_against_the_current_version_is_committed(db, case_id):
    _parked(db, case_id, version=3)

    result = commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")

    assert result.committed is True
    assert result.case_version == 3
    assert result.reason == ""


def test_a_committed_approval_is_recorded_on_the_case(db, case_id):
    _parked(db, case_id, version=3)

    commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")

    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["approval"]["decision"] == APPROVED
    assert stored["approval"]["approval_id"] == "APR-1"
    assert stored["approval"]["actor"] == "reviewer@example.com"
    assert stored["approval"]["case_version"] == 3


def test_a_committed_approval_writes_its_own_subcollection_document(db, case_id):
    """The case block is the current decision; the subcollection is the ledger.
    A later approval overwrites the block, and the ledger still shows both."""
    _parked(db, case_id, version=3)

    commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")

    doc = (db.collection(CASES).document(case_id)
             .collection(APPROVALS).document("APR-1").get())
    assert doc.exists
    assert doc.to_dict()["decision"] == APPROVED


def test_the_gate_verdict_is_never_overwritten_by_an_approval(db, case_id):
    """The whole argument for a separate block. The machine's verdict is the
    auditable artefact; an approval that rewrote `policy.band` would erase the
    thing being audited."""
    _parked(db, case_id, version=3)

    commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")

    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["policy"]["band"] == "review"


def test_replaying_the_same_approval_is_rejected_as_a_duplicate(db, case_id):
    _parked(db, case_id, version=3)
    commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")

    result = commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")

    assert result.committed is False
    assert result.reason == "duplicate_approval"


def test_an_approval_against_an_older_version_is_rejected_as_stale(db, case_id):
    """The human approved what they saw at version 3; the case is now at 5.
    Whatever they read is not what would execute."""
    _parked(db, case_id, version=5)

    result = commit_approval(db, case_id, "APR-2", 3, APPROVED, "reviewer@example.com")

    assert result.committed is False
    assert result.reason == "stale_approval"
    assert result.case_version == 5


def test_an_approval_for_a_case_that_is_not_parked_is_rejected(db, case_id):
    db.collection(CASES).document(case_id).set({
        "case_id": case_id, "case_version": 2, "phase": "processing",
    })

    result = commit_approval(db, case_id, "APR-3", 2, APPROVED, "reviewer@example.com")

    assert result.committed is False
    assert result.reason == "not_awaiting"


def test_an_approval_for_an_unknown_case_is_rejected(db, case_id):
    result = commit_approval(db, case_id, "APR-4", 1, APPROVED, "reviewer@example.com")

    assert result.committed is False
    assert result.reason == "unknown_case"


def test_an_unrecognised_decision_is_rejected(db, case_id):
    _parked(db, case_id, version=3)

    result = commit_approval(db, case_id, "APR-5", 3, "maybe", "reviewer@example.com")

    assert result.committed is False
    assert result.reason == "invalid_decision"


def test_a_replay_is_reported_as_duplicate_even_after_the_case_moved_on(db, case_id):
    """Check-order test. A genuinely-applied approval reaches this function
    again only AFTER its own effects — the case has left awaiting_approval and
    advanced its version. Under any other order the honest reason (this is a
    replay) would be masked by a consequence of the first call working."""
    _parked(db, case_id, version=3)
    commit_approval(db, case_id, "APR-6", 3, APPROVED, "reviewer@example.com")
    db.collection(CASES).document(case_id).set(
        {"case_version": 4, "phase": "committed"}, merge=True
    )

    result = commit_approval(db, case_id, "APR-6", 3, APPROVED, "reviewer@example.com")

    assert result.reason == "duplicate_approval"


def test_a_rejection_is_committed_the_same_way(db, case_id):
    _parked(db, case_id, version=3)

    result = commit_approval(db, case_id, "APR-7", 3, REJECTED, "reviewer@example.com")

    assert result.committed is True
    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["approval"]["decision"] == REJECTED


def test_two_concurrent_commits_of_the_same_approval_admit_exactly_one(db, case_id):
    """The reason this is a transaction and not two statements. A double-click
    or a client retry sends the same approval twice at once; both transactions
    read "not yet committed" before either writes, and only Firestore's
    optimistic concurrency can break the tie."""
    from concurrent.futures import ThreadPoolExecutor

    _parked(db, case_id, version=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(commit_approval, db, case_id, "APR-RACE", 3, APPROVED, "r@example.com")
            for _ in range(2)
        ]
        results = [f.result() for f in futures]

    assert sum(1 for r in results if r.committed) == 1, (
        f"exactly one commit must win, got {[(r.committed, r.reason) for r in results]}"
    )
