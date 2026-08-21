"""The decision path, end to end, over state park_case actually writes.

This is the composition tests/unit/test_approval_release.py proves in the
abstract: commit, then drain if committed. Here it runs through the real
handler, with the reviewer's identity injected the way the framework injects
it and the ERP client stubbed the way the executor tests stub it.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.executor import runner as runner_module
from app.nodes import park_case
from app.state.commands import DONE, PENDING, get_command
from app.state.firestore import CASES, claim_event
from console.iap import require_reviewer
from console.review import api
from console.store import list_awaiting_cases, list_cases


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
    """No override: the real dependency runs and must refuse.

    Saves and restores `api.dependency_overrides` rather than only clearing
    it: `api` is a module-global `FastAPI` instance shared across every test
    in this file (and, under `-p xdist`, potentially interleaved with other
    tests importing the same module), so a fixture that clears but never
    restores leaves whatever override was in place before this test to be
    silently lost for whoever runs after it.
    """
    saved = api.dependency_overrides.copy()
    api.dependency_overrides.clear()
    try:
        yield TestClient(api, raise_server_exceptions=False)
    finally:
        api.dependency_overrides.clear()
        api.dependency_overrides.update(saved)


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


def test_a_decision_with_a_missing_assertion_is_refused_with_403(
    db, case_id, anonymous_client, created, monkeypatch
):
    """The 403 branch specifically: an audience IS configured, but no
    identity assertion is presented. Distinct from the 503 case above, which
    exercises the unconfigured-audience branch instead — verify_token is
    never even reached there."""
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    version = _park_a_real_case(db, case_id)
    response = anonymous_client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
    )
    assert response.status_code == 403
    assert created == [], "no ERP write may happen without a verified reviewer"
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_the_review_queue_is_not_reachable_anonymously(anonymous_client, monkeypatch):
    # IAP_AUDIENCE must be set here, same as the 403 test above: unset, every
    # one of these lands in _audience()'s 503 branch before verify_token is
    # ever reached, and would keep passing even with require_reviewer's
    # entire verification body deleted. Pinning 403 (not `in (403, 503)`)
    # is what actually exercises the guard this test claims to cover.
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    response = anonymous_client.get("/review")
    assert response.status_code == 403


def test_a_case_page_is_not_reachable_anonymously(anonymous_client, case_id, monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    response = anonymous_client.get(f"/review/{case_id}")
    assert response.status_code == 403


def test_healthz_is_not_reachable_anonymously(anonymous_client, monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    response = anonymous_client.get("/healthz")
    assert response.status_code == 403


def test_a_cross_site_decision_is_refused(db, case_id, client, created):
    """Sec-Fetch-Site is set by the browser, never by page script — an
    attacker page auto-submitting a hidden form to this endpoint cannot
    forge it away, so a value other than same-origin/none is a reliable
    tell that the request did not originate on this page."""
    version = _park_a_real_case(db, case_id)
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
    assert created == [], "no ERP write may happen off a cross-site submission"
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_a_mismatched_origin_decision_is_refused(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403
    assert created == []
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_an_origin_differing_only_by_scheme_is_permitted(db, case_id, client, created):
    """Regression pin for the scheme-comparison defect: behind Cloud Run and
    IAP this container is reached over plain HTTP (request.url.scheme ==
    "http") while every real browser Origin reads "https" — comparing scheme
    would refuse every legitimate decision in production. Only the host may
    gate the request; TestClient's own host is "testserver", so an Origin
    that names that same host under a different scheme must be permitted
    all the way through to a committed, released decision."""
    version = _park_a_real_case(db, case_id)
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
        headers={"Origin": "https://testserver"},
    )
    assert response.status_code == 200
    assert created == ["Andes Foods"]
    assert get_command(db, case_id, "create_supplier", 1)["status"] == DONE


def test_an_invalid_decision_is_refused(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "maybe", "expected_case_version": version},
    )
    assert response.status_code == 200
    assert "not a decision this service accepts" in response.text.lower()
    assert created == []
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_a_case_not_parked_for_review_is_refused(db, case_id, client, created):
    """claim_event alone (no park_case) leaves the case at phase
    "processing", never "awaiting_approval" — commit_approval must refuse
    a decision about a case that was never parked."""
    claim_event(db, case_id, "EVT-1", {
        "event_type": "new_supplier_packet",
        "supplier": "Andes Foods",
    })
    response = client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": 1},
    )
    assert response.status_code == 200
    assert "not parked for review" in response.text.lower()
    assert created == []


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
    # The losing reviewer must be able to tell WHAT happened, not just that
    # they were too late — the recorded decision, never the actor, is shown.
    assert "approved" in second.text.lower()
    assert created == ["Andes Foods"]


def test_a_decided_case_drops_off_the_review_queue(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
    )
    response = client.get("/review")
    assert response.status_code == 200
    assert case_id not in response.text


def test_a_long_parked_case_still_appears_in_the_review_queue(db, case_id, client):
    """The shared keplaria-test `cases` collection holds thousands of
    documents, so a case with an `updated_at` this old is nowhere near the
    50 most-recently-touched ones. That makes this the case that would
    silently vanish if the review queue were ever built on `list_cases`
    (recent-first, capped at 50) instead of `list_awaiting_cases` (every
    `awaiting_approval` case, no cap). Written directly rather than through
    `park_case`, because every real writer stamps `updated_at` with
    `SERVER_TIMESTAMP` — there is no way to make the pipeline itself produce
    a document this stale.
    """
    ancient = datetime(2000, 1, 1, tzinfo=timezone.utc)
    db.collection(CASES).document(case_id).set({
        "case_id": case_id,
        "phase": "awaiting_approval",
        "updated_at": ancient,
    })
    try:
        assert case_id in [c.get("case_id") for c in list_awaiting_cases(db)]
        assert case_id not in [c.get("case_id") for c in list_cases(db, limit=50)], (
            "sanity check: this case must be old enough to fall out of the "
            "50 most-recently-touched documents, or the queue assertion "
            "below would pass for the wrong reason"
        )

        response = client.get("/review")
        assert response.status_code == 200
        assert case_id in response.text, (
            "a long-parked case must not vanish from a queue that claims "
            "to show everything awaiting a decision"
        )
    finally:
        db.collection(CASES).document(case_id).delete()


def test_a_decided_case_page_no_longer_offers_a_decision(db, case_id, client, created):
    version = _park_a_real_case(db, case_id)
    client.post(
        f"/review/{case_id}/decide",
        data={"decision": "approved", "expected_case_version": version},
    )
    response = client.get(f"/review/{case_id}")
    assert response.status_code == 200
    assert "approved" in response.text.lower()
    assert 'name="decision"' not in response.text


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


def test_failures_page_requires_a_reviewer(anonymous_client, monkeypatch):
    """Raw destination error strings must stay behind IAP.

    IAP_AUDIENCE must be set and the assertion must pin 403 exactly. Unset,
    the request lands in _audience()'s 503 branch before verify_token is ever
    reached, and the test would keep passing with require_reviewer's entire
    verification body deleted — the same trap the sibling anonymous tests in
    this file document.
    """
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")

    response = anonymous_client.get("/review/failures")

    assert response.status_code == 403


def test_failures_page_is_not_swallowed_by_the_case_route(client, monkeypatch):
    """/review/{case_id} is declared in this same app. If `failures` is read as
    a case ID it does NOT 404 — review_case renders review_result.html with
    status 200 and `<h1>{{ case_id }}</h1>` showing the literal string
    "failures", which would satisfy a status-200-and-"failures"-in-body
    assertion for the wrong reason. The "no such case" phrase is that
    fallback page's tell and appears nowhere on review_failures.html, so
    its absence is what actually proves this page rendered rather than the
    case route's not-found fallback.
    """
    import console.review as review

    monkeypatch.setattr(review, "list_failed_commands", lambda db, limit=50: [])
    monkeypatch.setattr(review, "list_dead_events", lambda db, limit=50: [])

    response = client.get("/review/failures")

    assert response.status_code == 200
    assert "failures" in response.text.lower()
    assert "no such case" not in response.text.lower()


def test_the_review_page_shows_a_department_denial(db, case_id, client):
    """A quarantined-style routing record with a refusal must render.
    Sentinel refusal string, deliberately distinctive: a generic substring
    ('refused', a department name) matches unrelated template text and
    would pass vacuously."""
    from app.state.firestore import CASES

    db.collection(CASES).document(case_id).set({
        "case_id": case_id,
        "case_version": 1,
        "phase": "awaiting_approval",
        "supplier": "Andes Foods",
        "policy": {"policy_id": "supplier_risk", "policy_version": 2,
                   "score": 0.25, "band": "review",
                   "factors_fired": [], "reasons": []},
        "screening": {"reachable": True, "flagged": [],
                      "candidate_count": 0, "candidates": []},
        "routing": {
            "proposed": ["evidence"],
            "route": [],
            "dropped": [],
            "reason": "over-reach",
            "refused": "DEPARTMENT_FORBIDS_AGENT: sentinel-denial-42",
            "department": "finance",
            "department_source": "event",
        },
    })

    response = client.get(f"/review/{case_id}")

    assert response.status_code == 200
    assert "sentinel-denial-42" in response.text
    assert "finance" in response.text


def test_failures_page_shows_a_dead_command_and_a_dead_event(client, monkeypatch):
    import console.review as review

    monkeypatch.setattr(
        review,
        "list_failed_commands",
        lambda db, limit=50: [
            {
                "case_id": "CASE-STUCK",
                "action": "create_supplier",
                "status": "dead",
                "execution_attempts": 5,
                "error": "FrappeError: ERP down",
            }
        ],
    )
    monkeypatch.setattr(
        review,
        "list_dead_events",
        lambda db, limit=50: [
            {"event_id": "evt-1", "case_id": "CASE-LOST", "delivery_attempt": 5}
        ],
    )

    response = client.get("/review/failures")

    assert response.status_code == 200
    assert "CASE-STUCK" in response.text
    assert "CASE-LOST" in response.text
    assert "ERP down" in response.text
