"""Events that exhausted Pub/Sub redelivery, kept so they are not simply lost.

Without this, a message the ingress kept rejecting was retried until the
subscription's 7-day retention window expired and then silently dropped —
no record that the event ever existed, and a case that just never advanced.
The keplaria-events-push subscription now carries a deadLetterPolicy, and
the dead-letter topic pushes to POST /pubsub/dead, which writes here.

Keyed by event_id, so a repeat delivery of the same dead-lettered message
updates one document instead of accumulating near-duplicates. `first_seen`
is written only on creation: it answers "how long has this been stuck",
which is the question worth asking, and merge-writing it on every delivery
would silently destroy the answer.
"""

from __future__ import annotations

from google.cloud import firestore

DEAD_EVENTS = "dead_events"


def record_dead_event(
    db: firestore.Client,
    event_id: str,
    case_id: str | None,
    delivery_attempt: int,
    payload: dict,
) -> None:
    """Record (or refresh) a dead-lettered event.

    Transactional because `first_seen` is create-only: a plain
    `set(merge=True)` would overwrite it on every repeat delivery, and a
    read-then-write outside a transaction would race two concurrent
    deliveries of the same message into both believing they were first.
    """
    ref = db.collection(DEAD_EVENTS).document(event_id)

    @firestore.transactional
    def _record(txn: firestore.Transaction) -> None:
        snap = ref.get(transaction=txn)
        record = {
            "event_id": event_id,
            "case_id": case_id,
            "delivery_attempt": delivery_attempt,
            "payload": payload,
            "last_seen": firestore.SERVER_TIMESTAMP,
        }
        if not snap.exists:
            record["first_seen"] = firestore.SERVER_TIMESTAMP
            txn.set(ref, record)
        else:
            txn.update(ref, record)

    _record(db.transaction())


def list_dead_events(db: firestore.Client, limit: int = 50) -> list[dict]:
    """Most recently dead-lettered events first.

    Ordered by `last_seen`, which every write sets — an `order_by` on a field
    some documents lack does not merely sort, it FILTERS those documents out
    of the result entirely.
    """
    query = db.collection(DEAD_EVENTS).order_by(
        "last_seen", direction=firestore.Query.DESCENDING
    ).limit(limit)
    return [snap.to_dict() or {} for snap in query.stream()]
