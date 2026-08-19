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


def case_id_is_addressable(case_id: str) -> bool:
    """False for a `case_id` Firestore can never hold as a document.

    `.document(case_id)` treats "/" as a path separator, so a case_id like
    "FOO/BAR" turns one collection segment into three ("cases", "FOO",
    "BAR") — an odd count, which `DocumentReference` rejects outright with a
    bare `ValueError` ("A document must have an even number of path
    elements"): an unhandled 500 rather than a refusal, on whichever route
    reaches it first. This id shape is only reachable via a URL-encoded `/`
    (`%2F`) in the request path — a literal `/` would just split the URL
    into more path segments and never arrive here as part of `case_id` at
    all — but an encoded one decodes to a real `/` by the time a route
    handler sees it, on both the public detail page and the review
    service's decide route. `load_case` uses this as its own guard; the
    decide route (which never calls `load_case` on its main path — see
    console/review.py) checks it directly before building anything that
    would otherwise construct that same invalid reference.
    """
    return "/" not in case_id


def load_case(db: firestore.Client, case_id: str) -> tuple[dict | None, list[dict]]:
    """Return the raw case document and its outbox commands, or (None, [])."""
    if not case_id_is_addressable(case_id):
        return None, []
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
    not sorted to one end, dropped. Every writer of a case document sets
    `updated_at`: app.state.firestore.claim_event on the ingress path,
    app.nodes._record_outcome and app.nodes._claim_lifecycle_commands on the
    graph's own writes (the latter covers a lifecycle advance with no new
    routing or policy verdict to report — a clock tick alone), and
    app.state.approvals.commit_approval when a human decision lands. A new
    writer that skips it makes its case permanently invisible here, with
    nothing anywhere to say so — record_success/record_failure in
    app.state.commands are not such a writer: they refresh only the outbox
    command document, never the case document itself, so they have no
    `updated_at` to set and do not move a case's position in this ordering.

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


def list_awaiting_cases(db: firestore.Client) -> list[dict]:
    """Every case currently parked for review — complete, by requirement.

    Deliberately not built on `list_cases`: that query orders by
    `updated_at` and stops at `limit` (50), which is right for "recent
    cases" on the public console and wrong for a queue that claims to show
    everything awaiting a decision. A case parked long enough to fall out of
    the 50 most-recently-touched documents must not vanish from here just
    because it has been waiting quietly.

    The query is a single equality `where` on `phase`, ordered by document
    name. That combination is deliberate, not an oversight: an `order_by` on
    a *different* field (`updated_at`, say) alongside the filter would
    require a composite index, and this project has none — it would work
    against the local/emulator Firestore that auto-creates indexes on demand,
    then fail at runtime the moment it ran against the real deployed
    database. `__name__` is the one ordering the single-field index on
    `phase` already serves, so it costs nothing at deploy time (verified
    against the real `keplaria-test` database, not assumed). Case documents
    are keyed by `case_id`, so this is an ascending sort by case id, done by
    Firestore instead of in Python.

    **A ceiling was tried here on 2026-08-19 and removed.** The obvious
    hardening — `.limit(n)` plus a "this read was cut" flag on the page — is
    wrong for this particular query, and the reason is worth keeping. Cutting
    the read means some parked cases are not listed; on an approval queue
    that is not a display concession, it is a case nobody is told to decide.
    The measurement that settled it: `keplaria-test` holds 1964 documents in
    `awaiting_approval` (5258 cases total, almost all residue from earlier
    runs), and under any ceiling below that, the regression test asserting a
    long-parked case still reaches this queue fails — the bound removes
    exactly the property the queue exists to guarantee. A complete read is
    therefore the requirement, and the growth it implies is answered by
    clearing the queue, not by hiding its tail. If this collection ever grows
    past what one page should hold, the answer is cursor pagination over this
    same `__name__` ordering, which keeps every case reachable; it is not a
    truncating limit.
    """
    query = (
        db.collection(CASES)
        .where(filter=firestore.FieldFilter("phase", "==", "awaiting_approval"))
        .order_by("__name__")
    )
    return [doc.to_dict() or {} for doc in query.stream()]


def _touched_at(row: dict) -> tuple[int, float]:
    """A total, type-safe newest-first sort key for an outbox row.

    Outbox documents are schemaless, and the two timestamp fields are not
    guaranteed to be there: `updated_at` and `created_at` are written as
    server timestamps, so a document read back mid-write, one written by an
    older version of this code, or one hand-repaired by an operator can hold
    neither. The obvious key — `updated_at or created_at or 0` — then mixes
    `DatetimeWithNanoseconds` with `int` inside one `sort()`, which raises
    `TypeError` and turns /review/failures into a 500 at exactly the moment
    someone is trying to look at a broken command.

    Everything is therefore reduced to one comparable type before sorting,
    and rows with no usable timestamp sort last under `reverse=True` (the
    leading 0) rather than being compared against a real one.
    """
    for field in ("updated_at", "created_at"):
        value = row.get(field)
        to_timestamp = getattr(value, "timestamp", None)
        if callable(to_timestamp):
            try:
                return (1, float(to_timestamp()))
            except (TypeError, ValueError, OSError, OverflowError):
                continue
        # bool is an int subclass; a stray True must not read as 1970.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (1, float(value))
    return (0, 0.0)


def list_failed_commands(db: firestore.Client, limit: int = 50) -> list[dict]:
    """Every command in a FAILED or DEAD state, across all cases.

    Two `in`-filtered collection-group queries would need a composite index
    apiece; this runs one equality query per status instead and merges in
    Python. At the scale this system operates — a handful of stuck commands
    is already an incident — that is the cheaper trade, and it keeps the
    index requirement to the single collection-group index the sweep already
    needs.

    Sorted newest-touched first in Python rather than with `order_by`:
    combining an equality filter on `status` with an ordering on
    `updated_at` requires a composite index, and an `order_by` on a field
    some older documents lack would FILTER them out rather than merely
    sorting them.

    DEAD rows claim their share of `limit` FIRST, and only the remainder is
    filled with FAILED ones. Merging both and truncating afterwards let 50
    failed commands push every dead command off the page — and a dead
    command is the one that will never be retried and therefore the one that
    actually needs a human, while a failed one is still being re-driven by
    the sweep. The page must not be able to hide the worse state behind the
    recoverable one.
    """
    from app.state.commands import DEAD, FAILED

    by_status: dict[str, list[dict]] = {}
    for status in (DEAD, FAILED):
        query = (
            db.collection_group(OUTBOX)
            .where(filter=firestore.FieldFilter("status", "==", status))
            .limit(limit)
        )
        by_status[status] = sorted(
            (snap.to_dict() or {} for snap in query.stream()),
            key=_touched_at,
            reverse=True,
        )

    rows = by_status[DEAD][:limit]
    rows.extend(by_status[FAILED][: max(0, limit - len(rows))])
    rows.sort(key=_touched_at, reverse=True)
    return rows
