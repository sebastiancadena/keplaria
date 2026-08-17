"""An event that exhausts redelivery must survive, not vanish at retention."""

from __future__ import annotations

import uuid

from app.state.dead_events import DEAD_EVENTS, list_dead_events, record_dead_event

PAYLOAD = {"event_type": "new_supplier_packet", "supplier": "Andes Verde SAS"}


def _event_id() -> str:
    return f"evt-dead-{uuid.uuid4().hex[:12]}"


def test_a_dead_event_is_recorded(db, case_id):
    event_id = _event_id()

    record_dead_event(db, event_id, case_id, 5, PAYLOAD)

    stored = db.collection(DEAD_EVENTS).document(event_id).get().to_dict()
    assert stored["event_id"] == event_id
    assert stored["case_id"] == case_id
    assert stored["delivery_attempt"] == 5
    assert stored["payload"]["event_type"] == "new_supplier_packet"
    assert stored["first_seen"] is not None
    assert stored["last_seen"] is not None


def test_a_repeat_delivery_updates_rather_than_duplicates(db, case_id):
    event_id = _event_id()
    record_dead_event(db, event_id, case_id, 5, PAYLOAD)
    first = db.collection(DEAD_EVENTS).document(event_id).get().to_dict()["first_seen"]

    record_dead_event(db, event_id, case_id, 6, PAYLOAD)

    stored = db.collection(DEAD_EVENTS).document(event_id).get().to_dict()
    assert stored["delivery_attempt"] == 6
    assert stored["first_seen"] == first, "first_seen must not be overwritten"


def test_dead_events_are_listed(db, case_id):
    event_id = _event_id()
    record_dead_event(db, event_id, case_id, 5, PAYLOAD)

    listed = list_dead_events(db)

    assert any(e["event_id"] == event_id for e in listed)
