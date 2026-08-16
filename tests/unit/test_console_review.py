"""The decision path, end to end, over state park_case actually writes.

This is the composition tests/unit/test_approval_release.py proves in the
abstract: commit, then drain if committed. Here it runs through the real
handler, with the reviewer's identity injected the way the framework injects
it and the ERP client stubbed the way the executor tests stub it.
"""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient

from app.executor import runner as runner_module
from app.nodes import park_case
from app.state.commands import DONE, PENDING, get_command
from app.state.firestore import claim_event
from console.iap import require_reviewer
from console.review import api


class _StubContext:
    def __init__(self, state: dict):
        self.state = state


@contextlib.contextmanager
def _fake_client():
    yield object()


@pytest.fixture
def created(monkeypatch):
    """Record ERP writes instead of performing them."""
    recorded: list[str] = []
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module,
        "create_supplier_if_absent",
        lambda client, supplier, email_id="": recorded.append(supplier)
        or {"external_id": supplier, "created": True},
    )
    return recorded


@pytest.fixture
def client():
    api.dependency_overrides[require_reviewer] = lambda: "reviewer@example.com"
    yield TestClient(api)
    api.dependency_overrides.clear()


@pytest.fixture
def anonymous_client():
    """No override: the real dependency runs and must refuse."""
    api.dependency_overrides.clear()
    return TestClient(api, raise_server_exceptions=False)


def _park_a_real_case(db, case_id: str) -> int:
    claim = claim_event(db, case_id, "EVT-1", {
        "event_type": "new_supplier_packet",
        "supplier": "Andes Foods",
    })
    ctx = _StubContext({
        "case": {
            "case_id": case_id,
            "event_type": "new_supplier_packet",
            "supplier": "Andes Foods",
            "effective_date": "2026-08-16",
        },
        "screening": {
            "endpoint": "http://10.10.0.2:8000", "supplier": "Andes Foods",
            "reachable": True, "error": None, "flagged": [],
            "candidates": [{"id": "syn-co-008", "score": 0.526, "match": False}],
        },
        "policy": {"policy_id": "supplier_risk", "policy_version": 2, "score": 0.25,
                   "band": "review", "factors_fired": [], "reasons": []},
    })
    park_case(None, ctx)
    return claim.case_version


def test_a_decision_without_a_verified_identity_is_refused(
    db, case_id, anonymous_client, created, monkeypatch
):
    monkeypatch.delenv("IAP_AUDIENCE", raising=False)
    version = _park_a_real_case(db, case_id)
    response = anonymous_client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
    )
    assert response.status_code in (403, 503)
    assert created == [], "no ERP write may happen without a verified reviewer"
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_the_review_page_names_the_commands_that_will_run(db, case_id, client):
    _park_a_real_case(db, case_id)
    response = client.get(f"/review/{case_id}")
    assert response.status_code == 200
    assert "create_supplier" in response.text


def test_approving_releases_the_work(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
    )
    assert response.status_code == 200
    assert created == ["Andes Foods"]
    assert get_command(db, case_id, "create_supplier", 1)["status"] == DONE


def test_rejecting_leaves_the_work_unexecuted(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "rejected", "expected_case_version": version},
    )
    assert response.status_code == 200
    assert created == []
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_a_double_submit_writes_to_the_erp_exactly_once(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    payload = {"decision": "approved", "expected_case_version": version}
    client.post(f"/review/{case_id}/decide", data=payload)
    second = client.post(f"/review/{case_id}/decide", data=payload)
    assert second.status_code == 200
    assert "already decided" in second.text.lower()
    assert created == ["Andes Foods"]


def test_a_stale_version_is_refused(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version - 1},
    )
    assert response.status_code == 200
    assert "moved on" in response.text.lower()
    assert created == []
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_an_unknown_case_is_refused(db, client, created):
    response = client.post(
        "/review/NOPE-1/decide",
        data={"decision": "approved", "expected_case_version": 1},
    )
    assert response.status_code == 200
    assert "no such case" in response.text.lower()
    assert created == []
