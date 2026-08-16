"""The public console, driven over state the graph actually writes.

Setup runs the real park_case rather than hand-writing a parked case. State
that only a test knows how to build is how a feature ships inert with a green
suite; this project has already paid that bill once.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.nodes import park_case
from app.state.approvals import APPROVED, commit_approval
from app.state.firestore import claim_event
from console.public import api


class _StubContext:
    def __init__(self, state: dict):
        self.state = state


@pytest.fixture
def client():
    return TestClient(api)


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


def test_the_case_list_renders_a_parked_case(db, case_id, client):
    _park_a_real_case(db, case_id)
    response = client.get("/")
    assert response.status_code == 200
    assert case_id in response.text
    assert "Andes Foods" in response.text


def test_the_detail_page_shows_the_gate_verdict_and_the_commands(db, case_id, client):
    _park_a_real_case(db, case_id)
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200
    assert "Gate: review" in response.text
    assert "create_supplier" in response.text


def test_the_detail_page_never_shows_the_screening_endpoint(db, case_id, client):
    _park_a_real_case(db, case_id)
    response = client.get(f"/cases/{case_id}")
    assert "10.10.0.2" not in response.text


def test_the_detail_page_never_shows_the_approval_actor(db, case_id, client):
    version = _park_a_real_case(db, case_id)
    commit_approval(db, case_id, f"{case_id}:v{version}", version, APPROVED,
                    "reviewer@example.com")
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200
    assert "reviewer@example.com" not in response.text


def test_the_detail_page_shows_both_bands_when_they_differ(db, case_id, client):
    version = _park_a_real_case(db, case_id)
    commit_approval(db, case_id, f"{case_id}:v{version}", version, APPROVED,
                    "reviewer@example.com")
    response = client.get(f"/cases/{case_id}")
    assert "Gate: review" in response.text, "the gate's own verdict must stay visible"
    assert "Effective: clear" in response.text, "the effective band must be visible"


def test_a_subthreshold_candidate_is_shown(db, case_id, client):
    _park_a_real_case(db, case_id)
    response = client.get(f"/cases/{case_id}")
    assert "syn-co-008" in response.text
    assert "0.526" in response.text


def test_an_unknown_case_is_a_404(client):
    """A judge mistyping a case id must land on the rendered not_found.html
    page, not FastAPI's default `{"detail": ...}` JSON body. Asserting only
    the status code would keep passing even if the handler were rewritten to
    `raise HTTPException(404, "no such case")` instead of rendering the
    template — that response is also a 404. The content-type, the masthead
    marker only base.html produces, and the absence of "detail" together
    pin the template, not just the status.
    """
    response = client.get("/cases/NOPE-1")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "masthead" in response.text
    assert "detail" not in response.text


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
