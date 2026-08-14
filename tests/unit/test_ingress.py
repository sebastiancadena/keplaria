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
