"""The public console, driven over state the graph actually writes.

Setup runs the real park_case rather than hand-writing a parked case. State
that only a test knows how to build is how a feature ships inert with a green
suite; this project has already paid that bill once.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.nodes import commit_commands, park_case
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
        "routing": {
            "proposed": ["evidence", "compliance"],
            "route": ["evidence", "compliance"],
            "dropped": ["compliance"],
            "reason": "new supplier",
            "refused": None,
            "department": "dept-sentinel-7",
            "department_source": "event",
            "evidence_skipped_no_document": False,
            "evidence_skipped_tainted_document": False,
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


def test_the_case_list_links_to_the_fleet_page(client):
    """The fleet page (this branch's surface) was otherwise unreachable by
    navigation — no template linked to it and it offered no way back."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/fleet"' in response.text


def test_the_detail_page_shows_a_dropped_agent_distinctly_from_a_refusal(
    db, case_id, client
):
    """The 'drop' half of refuse/drop, asserted at the template layer: the
    fixture's routing.dropped (see _park_a_real_case) is non-empty while
    routing.refused stays None, so this pins the narrowing line as a thing
    that renders independent of a refusal, not merely alongside one."""
    _park_a_real_case(db, case_id)
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200
    assert "Dropped by policy" in response.text


def test_the_templates_refuse_a_withheld_field_the_view_model_hands_them(
    db, case_id, client, monkeypatch
):
    """The template layer, pinned separately from the allowlist.

    Replaces two earlier tests that fetched the detail page and asserted the
    screening endpoint and the approval actor were absent from the body.
    Neither could fail. Measured 2026-08-19 by mutation: adding
    `"endpoint": screening.get("endpoint")` and `"actor":
    approval.get("actor")` to console.projection.public_case leaves both of
    those assertions passing and is caught only by test_console_projection's
    own withheld-field tests. The reason is structural — case.html names the
    fields it prints, and Jinja renders a path the view model does not carry
    as the empty string, so a leak upstream never reaches the text those
    assertions searched. A negative assertion over a surface that cannot
    produce the value is a test that reports on nothing.

    So the leak is injected here instead. The view model is made to carry
    both withheld values, and the rendered page must still not show them.
    That fails the moment a template starts reading `case.screening.endpoint`
    or `case.approval.actor` — the one failure mode the allowlist cannot
    catch by itself, since a template may read any path it likes.
    """
    import console.public as public

    real = public.public_case

    def leaky(case, commands=()):
        view = real(case, commands)
        view["screening"]["endpoint"] = "http://10.10.0.2:8000"
        view["approval"] = {**(view.get("approval") or {}), "actor": "reviewer@example.com"}
        return view

    version = _park_a_real_case(db, case_id)
    commit_approval(db, case_id, f"{case_id}:v{version}", version, APPROVED,
                    "reviewer@example.com")
    monkeypatch.setattr(public, "public_case", leaky)

    response = client.get(f"/cases/{case_id}")

    assert response.status_code == 200
    assert "10.10.0.2" not in response.text, "case.html must not print the endpoint"
    assert "reviewer@example.com" not in response.text, (
        "case.html must not print the approval actor"
    )


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


def test_the_detail_page_shows_the_department_chip(db, case_id, client):
    """Sentinel value, not a real department name — 'procurement' appears
    in enough other copy that asserting it could pass vacuously."""
    _park_a_real_case(db, case_id)
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200
    assert "dept-sentinel-7" in response.text


def test_the_detail_page_shows_a_command_refused_by_department(db, case_id, client):
    """A department-scope refusal at claim time leaves no outbox row, but it
    must still reach the case page: app.nodes._claim_lifecycle_commands
    persists it onto the case document precisely because console/store.py's
    load_case builds its command list from the outbox subcollection alone.
    Driven through the real commit_commands terminal, same principle as this
    module's park_case-based setup above — hand-writing the refusal onto the
    case document would prove nothing about whether the graph itself ever
    produces it."""
    ctx = _StubContext({
        "case": {
            "case_id": case_id,
            "event_type": "renewal_due",
            "supplier": "Andes Foods",
            "effective_date": "2026-08-20",
            "department": "finance",
        },
        "case_state": {
            "supplier": "Andes Foods",
            "lifecycle": {"state": "active", "cycle": 1},
            "certificate": {"expiry_date": "2026-09-01", "evidence_version": 1},
        },
    })
    commit_commands(None, ctx)

    response = client.get(f"/cases/{case_id}")

    assert response.status_code == 200
    assert "request_renewal" in response.text
    assert "refused and recorded" in response.text


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
