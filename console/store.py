"""Reads for the console. No writes exist here, by construction.

Both services read case state the same way; only the review service is allowed
to act on it. No route in this app calls a write. That is a claim about the
route table, not about the module graph: console.public imports
app.state.firestore, whose namespace also holds claim_event/mark_dispatched,
and console.projection imports app.executor.runner, which imports
app.executor.frappe (the ERP writes) and app.state.commands' record_success/
record_failure. Nothing here calls any of them, but their presence on the
import graph means the actual enforcement boundary is the route table (see
console/public.py) plus whatever Firestore IAM role this app is deployed
under — not the shape of what got imported.
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
    nothing at deploy time.

    The behaviour that matters most for whoever next touches this query:
    `order_by` does not merely sort, it FILTERS. A document missing the
    `updated_at` field is silently excluded from the result set entirely —
    not sorted to one end, dropped. Every writer of a case document must set
    `updated_at` (see app.nodes._record_outcome and
    app.state.approvals.commit_approval) or its case becomes permanently
    invisible here, with nothing anywhere to say so.

    The "parked first" pass still happens in Python: that ranking has
    nothing to do with recency and would need a second, composite index to
    express as a Firestore-side sort.
    """
    query = db.collection(CASES).order_by(
        "updated_at", direction=firestore.Query.DESCENDING
    ).limit(limit)
    cases = [doc.to_dict() or {} for doc in query.stream()]
    cases.sort(
        key=lambda c: (c.get("phase") != "awaiting_approval", str(c.get("case_id") or ""))
    )
    return cases
