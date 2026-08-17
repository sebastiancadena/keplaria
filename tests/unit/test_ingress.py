"""The push handler must ack duplicates without invoking the engine.

Redelivery is normal Pub/Sub behaviour, not an error, so a duplicate has to
return 200 — a non-2xx would make Pub/Sub redeliver forever. The opposite
case matters just as much: an engine failure after a successful claim must
return non-2xx so Pub/Sub retries, or the case would be stranded forever.
"""

import base64
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.schemas import CanonicalEvent


@pytest.fixture
def client(monkeypatch, db):
    import ingress.main as main

    calls = []
    monkeypatch.setattr(main, "invoke_engine", lambda event: calls.append(event) or {})
    # Point the handler at the test database; without this the unit test would
    # write cases into the database the deployed system uses.
    monkeypatch.setattr(main, "get_client", lambda: db)
    main.api.state.calls = calls
    return TestClient(main.api)


def _push(event: dict) -> dict:
    return {
        "message": {
            "data": base64.b64encode(json.dumps(event).encode()).decode(),
            "messageId": uuid.uuid4().hex,
        }
    }


def _event(case_id: str) -> dict:
    return {
        "event_id": f"evt-{uuid.uuid4().hex[:8]}",
        "case_id": case_id,
        "event_type": "new_supplier_packet",
        "supplier": "Comercializadora Andes Verde SAS",
        "schema_version": 1,
    }


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_first_delivery_claims_and_invokes(client, case_id):
    event = _event(case_id)

    response = client.post("/pubsub/push", json=_push(event))

    assert response.status_code == 200
    assert response.json()["status"] == "claimed"
    assert response.json()["case_version"] == 1
    assert len(client.app.state.calls) == 1


def test_redelivery_acks_without_invoking(client, case_id):
    event = _event(case_id)
    client.post("/pubsub/push", json=_push(event))

    response = client.post("/pubsub/push", json=_push(event))

    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"
    assert len(client.app.state.calls) == 1, "a duplicate must never reach the engine"


def test_malformed_event_is_acked_not_retried(client):
    response = client.post("/pubsub/push", json=_push({"nonsense": True}))

    assert response.status_code == 200
    assert response.json()["status"] == "invalid"
    assert client.app.state.calls == []


def test_malformed_envelope_is_rejected(client):
    assert client.post("/pubsub/push", json={"not": "an envelope"}).status_code == 400


def test_non_dict_envelope_is_rejected(client):
    assert client.post("/pubsub/push", json=["not", "an", "envelope"]).status_code == 400


def test_non_mapping_event_payload_is_acked_not_retried(client):
    response = client.post("/pubsub/push", json=_push(["not", "a", "mapping"]))

    assert response.status_code == 200
    assert response.json()["status"] == "invalid"
    assert client.app.state.calls == []


def test_engine_failure_is_retried_not_stranded(client, case_id, monkeypatch):
    """A claim already landed once the engine call runs; a transient engine
    failure must not silently drop the case — Pub/Sub has to redeliver it."""
    import ingress.main as main

    event = _event(case_id)
    attempts = []

    def flaky_invoke(evt):
        attempts.append(evt)
        if len(attempts) == 1:
            raise RuntimeError("engine unavailable")
        return {}

    monkeypatch.setattr(main, "invoke_engine", flaky_invoke)

    first = client.post("/pubsub/push", json=_push(event))
    assert first.status_code == 503

    second = client.post("/pubsub/push", json=_push(event))
    assert second.status_code == 200
    assert second.json()["status"] == "claimed"
    assert second.json()["case_version"] == 1, "a retry must not bump the version again"
    assert len(attempts) == 2, "the redelivered event must reach the engine again"


def test_the_event_carries_an_effective_date_and_document_reference():
    event = CanonicalEvent(
        event_id="E1", case_id="C1", event_type="certificate_received",
        supplier="Andes", effective_date="2027-01-20",
        document_ref="fixture:andes-verde-cert-2028",
    )

    assert event.effective_date == "2027-01-20"
    assert event.document_ref == "fixture:andes-verde-cert-2028"


def test_both_new_fields_are_optional():
    event = CanonicalEvent(
        event_id="E1", case_id="C1", event_type="new_supplier_packet",
        supplier="Andes",
    )

    assert event.effective_date is None
    assert event.document_ref is None


def test_a_dead_command_is_acked_not_retried(client, case_id, monkeypatch, caplog):
    """The cap inverts if this 503s: Pub/Sub would redeliver forever on the
    one command the system deliberately gave up on."""
    import logging

    import ingress.main as main

    monkeypatch.setattr(
        main,
        "execute_pending_commands",
        lambda db, cid: [
            {"action": "create_supplier", "status": "dead", "error": "HTTP 503"}
        ],
    )

    with caplog.at_level(logging.ERROR, logger="keplaria.ingress"):
        response = client.post("/pubsub/push", json=_push(_event(case_id)))

    assert response.status_code == 200
    assert response.json()["status"] == "claimed"
    # The dead block must execute and log at error level; without it, this
    # assertion fails, pinning the production code.
    assert "command execution is dead" in caplog.text
    assert "create_supplier" in caplog.text


def test_a_failed_command_still_returns_503(client, case_id, monkeypatch):
    """Below the cap, retrying is still the right answer."""
    import ingress.main as main

    monkeypatch.setattr(
        main,
        "execute_pending_commands",
        lambda db, cid: [
            {"action": "create_supplier", "status": "failed", "error": "HTTP 503"}
        ],
    )

    response = client.post("/pubsub/push", json=_push(_event(case_id)))

    assert response.status_code == 503


def test_a_dead_command_beside_a_failed_one_still_returns_503(client, case_id, monkeypatch):
    """If a result list has both dead and failed, the failed one still has
    retries left and the whole response must return 503 so Pub/Sub redelivers."""
    import ingress.main as main

    monkeypatch.setattr(
        main,
        "execute_pending_commands",
        lambda db, cid: [
            {"action": "archive_case", "status": "dead", "error": "Max retries reached"},
            {"action": "create_supplier", "status": "failed", "error": "HTTP 503"},
        ],
    )

    response = client.post("/pubsub/push", json=_push(_event(case_id)))

    assert response.status_code == 503


def test_admin_sweep_returns_the_summary(client, monkeypatch):
    import ingress.main as main

    monkeypatch.setattr(
        main,
        "sweep_failed_commands",
        lambda db: {
            "cases_swept": 2,
            "commands_driven": 3,
            "commands_refused": 4,
            "commands_dead": 1,
            "cases_skipped": 0,
            "case_ids": ["A", "B"],
        },
    )

    response = client.post("/admin/sweep")

    assert response.status_code == 200
    assert response.json()["cases_swept"] == 2
    assert response.json()["commands_dead"] == 1
    # The endpoint must pass the refused count through rather than fold it
    # into commands_driven: a backlog of cases waiting on a human decision is
    # the one thing a sweep summary must not report as work done.
    assert response.json()["commands_refused"] == 4


def test_admin_sweep_reports_a_failure_as_503(client, monkeypatch):
    """Cloud Scheduler retries on non-2xx, and a sweep that could not run at
    all is worth retrying — unlike an individual dead command."""
    import ingress.main as main

    def _boom(db):
        raise RuntimeError("firestore unreachable")

    monkeypatch.setattr(main, "sweep_failed_commands", _boom)

    response = client.post("/admin/sweep")

    assert response.status_code == 503


def test_dead_letter_push_records_the_event(client, case_id, db):
    from app.state.dead_events import DEAD_EVENTS

    event = _event(case_id)
    envelope = _push(event)
    envelope["message"]["deliveryAttempt"] = 5

    response = client.post("/pubsub/dead", json=envelope)

    assert response.status_code == 200
    stored = db.collection(DEAD_EVENTS).document(event["event_id"]).get().to_dict()
    assert stored["case_id"] == case_id
    assert stored["delivery_attempt"] == 5


def test_dead_letter_push_records_the_source_delivery_count(client, case_id, db):
    """The count comes from the attribute, not the `deliveryAttempt` field.

    Pub/Sub sets `deliveryAttempt` only on a subscription that itself has a
    dead-letter policy, and keplaria-events-dead-push deliberately has none —
    so on this endpoint that field is absent, exactly as it is here, and the
    real count arrives as CloudPubSubDeadLetterSourceDeliveryCount. Reading
    the field alone records 0 on every dead letter, next to a page saying the
    event was rejected five times.
    """
    from app.state.dead_events import DEAD_EVENTS

    event = _event(case_id)
    envelope = _push(event)
    envelope["message"]["attributes"] = {
        "CloudPubSubDeadLetterSourceDeliveryCount": "5"
    }

    response = client.post("/pubsub/dead", json=envelope)

    assert response.status_code == 200
    stored = db.collection(DEAD_EVENTS).document(event["event_id"]).get().to_dict()
    assert stored["delivery_attempt"] == 5


def test_dead_letter_push_survives_a_non_numeric_delivery_count(client, case_id, db):
    """The attribute is a string from an external system, and this handler is
    contractually obliged to always return 200. A count it cannot parse
    degrades to 0 rather than raising out of the only path that records the
    event at all."""
    from app.state.dead_events import DEAD_EVENTS

    event = _event(case_id)
    envelope = _push(event)
    envelope["message"]["attributes"] = {
        "CloudPubSubDeadLetterSourceDeliveryCount": "five"
    }

    response = client.post("/pubsub/dead", json=envelope)

    assert response.status_code == 200
    stored = db.collection(DEAD_EVENTS).document(event["event_id"]).get().to_dict()
    assert stored["delivery_attempt"] == 0


def test_dead_letter_push_acks_even_when_the_write_fails(client, monkeypatch, case_id):
    """The dead-letter path must never itself dead-letter."""
    import ingress.main as main

    def _boom(*a, **k):
        raise RuntimeError("firestore unreachable")

    monkeypatch.setattr(main, "record_dead_event", _boom)

    response = client.post("/pubsub/dead", json=_push(_event(case_id)))

    assert response.status_code == 200


def test_dead_letter_push_acks_an_unparseable_payload(client):
    """A malformed dead-lettered message is still acked, not rejected: it is
    the last stop, so there is nowhere left to retry to.

    Genuinely undecodable data, not merely a payload missing event_id — the
    point is to exercise the decode `except` branch, and valid base64 holding
    valid JSON would sail straight past it.
    """
    envelope = {"message": {"data": "!!!not-base64!!!", "messageId": "m-1"}}

    response = client.post("/pubsub/dead", json=envelope)

    assert response.status_code == 200


def test_dead_letter_push_falls_back_to_the_message_id(client, db):
    """An event whose body will not parse still deserves a durable record;
    Pub/Sub's own messageId is the only identity it has left.

    The messageId is generated per run rather than hardcoded: a fixed id
    would let a stale document from an earlier test run make this pass even
    if the write path were broken, since the shared emulator database is
    never wiped between runs.
    """
    from app.state.dead_events import DEAD_EVENTS

    message_id = f"m-fallback-{uuid.uuid4().hex[:12]}"
    envelope = {"message": {"data": "!!!not-base64!!!", "messageId": message_id}}

    response = client.post("/pubsub/dead", json=envelope)

    assert response.status_code == 200
    stored = db.collection(DEAD_EVENTS).document(message_id).get()
    assert stored.exists, "a dead letter with no parseable body must still be recorded"
