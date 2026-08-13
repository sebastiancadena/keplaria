"""The push handler must ack duplicates without invoking the engine.

Redelivery is normal Pub/Sub behaviour, not an error, so a duplicate has to
return 200 — a non-2xx would make Pub/Sub redeliver forever.
"""

import base64
import json
import uuid

import pytest
from fastapi.testclient import TestClient


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
