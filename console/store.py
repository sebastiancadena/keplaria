"""Reads for the console. No writes exist here, by construction.

Both services read case state the same way; only the review service is allowed
to act on it. Keeping the reads in one module means the public app's module
graph contains no write path at all.
"""

from __future__ import annotations

from google.cloud import firestore

from app.state.firestore import CASES, OUTBOX


def load_case(db: firestore.Client, case_id: str) -> tuple[dict | None, list[dict]]:
    """Return the raw case document and its outbox commands, or (None, [])."""
    snap = db.collection(CASES).document(case_id).get()
    if not snap.exists:
        return None, []
    commands = [
        doc.to_dict() or {}
        for doc in db.collection(CASES).document(case_id).collection(OUTBOX).stream()
    ]
    commands.sort(key=lambda c: (c.get("cycle") or 0, c.get("action") or ""))
    return snap.to_dict() or {}, commands


def list_cases(db: firestore.Client, limit: int = 50) -> list[dict]:
    """Recent cases, parked ones first.

    The Firestore side orders by `updated_at` descending before the limit is
    applied, so "recent" is answered by the query itself rather than by
    however `.limit()` happens to enumerate an unordered collection — with no
    `order_by` at all, `.limit(limit)` on a collection larger than `limit`
    could hand back any `limit` documents, and a case parked moments ago is
    not guaranteed to be among them. A single-field `order_by` needs no
    composite index (Firestore auto-indexes every field), so this costs
    nothing at deploy time. The "parked first" pass still happens in Python:
    that ranking has nothing to do with recency and would need a second,
    composite index to express as a Firestore-side sort.
    """
    query = db.collection(CASES).order_by(
        "updated_at", direction=firestore.Query.DESCENDING
    ).limit(limit)
    cases = [doc.to_dict() or {} for doc in query.stream()]
    cases.sort(
        key=lambda c: (c.get("phase") != "awaiting_approval", str(c.get("case_id") or ""))
    )
    return cases
