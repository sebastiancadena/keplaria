"""Unit tests for apply_route and quarantine_case — the fail-closed gate.

apply_route must route to 'blocked' on any coordinator proposal validate_route
rejects, and reach 'screen'/'skip' only for accepted proposals — including the
legitimately empty route for an event type that requires no agents at all.
quarantine_case, the 'blocked' terminal, must never claim the create_supplier
command or call the ERP — but it does record the refusal onto the case
document, so a reviewer (and verify.py) can see why a case was blocked.
"""

from __future__ import annotations

import app.nodes as nodes_module
from app.nodes import apply_route, quarantine_case
from app.state.commands import get_command
from app.state.firestore import CASES


class _StubContext:
    """Minimal stand-in for google.adk.agents.context.Context.

    apply_route and quarantine_case only ever read ctx.state, so a bare dict
    wrapper is enough — no real ADK Context needed.
    """

    def __init__(self, state: dict):
        self.state = state


def _case(case_id: str, event_type: str) -> dict:
    return {"case_id": case_id, "event_type": event_type}


def test_valid_proposal_with_compliance_screens():
    ctx = _StubContext({"case": _case("CASE-1", "new_supplier_packet")})
    node_input = {"route": ["evidence", "compliance"], "reason": "new supplier"}

    result = apply_route(node_input, ctx)

    assert result.actions.route == "screen"
    assert result.output["refused"] is None


def test_valid_proposal_without_compliance_skips():
    ctx = _StubContext({"case": _case("CASE-2", "certificate_received")})
    node_input = {"route": ["evidence"], "reason": "cert received"}

    result = apply_route(node_input, ctx)

    assert result.actions.route == "skip"
    assert result.output["refused"] is None


def test_unknown_agent_name_is_blocked_not_skipped():
    ctx = _StubContext({"case": _case("CASE-3", "new_supplier_packet")})
    node_input = {"route": ["finance_bot"], "reason": "hallucinated agent"}

    result = apply_route(node_input, ctx)

    assert result.actions.route == "blocked"
    assert result.output["refused"] is not None


def test_empty_route_when_agents_are_required_is_blocked_not_skipped():
    ctx = _StubContext({"case": _case("CASE-4", "new_supplier_packet")})
    node_input = {"route": [], "reason": "nothing needed"}

    result = apply_route(node_input, ctx)

    assert result.actions.route == "blocked"
    assert result.output["refused"] is not None


def test_empty_route_when_no_agents_are_required_skips_not_blocked():
    """The crux of the fix: 'no agents required' must not collapse into
    'refused'. evidence_overdue maps to an empty ALLOWED_ROUTES set, so an
    empty proposal is legitimate and must reach queue_supplier via 'skip',
    not be quarantined."""
    ctx = _StubContext({"case": _case("CASE-5", "evidence_overdue")})
    node_input = {"route": [], "reason": "deterministic, no agents needed"}

    result = apply_route(node_input, ctx)

    assert result.actions.route == "skip"
    assert result.output["refused"] is None


def test_unknown_event_type_is_blocked():
    ctx = _StubContext({"case": _case("CASE-6", "mystery_event")})
    node_input = {"route": [], "reason": "n/a"}

    result = apply_route(node_input, ctx)

    assert result.actions.route == "blocked"
    assert result.output["refused"] is not None


def test_quarantine_case_claims_no_command_but_records_the_refusal(
    db, case_id, monkeypatch
):
    # quarantine_case resolves its own Firestore client via get_client(),
    # which defaults to the live `(default)` database — the one the deployed
    # system uses. Point it at the `db` fixture's isolated test database
    # instead, so this test seeds and cleans up in the test database rather
    # than leaving a stray case document behind in production Firestore.
    monkeypatch.setattr(nodes_module, "get_client", lambda: db)
    # quarantine_case only ever runs on a case claim_event already created —
    # seed that precondition rather than relying on an unrealistic empty doc.
    db.collection(CASES).document(case_id).set({"case_id": case_id, "case_version": 1})

    routing = {
        "proposed": ["finance_bot"],
        "route": [],
        "reason": "hallucinated agent",
        "refused": "unknown agent: 'finance_bot'",
        "pending_implementation": [],
    }
    ctx = _StubContext(
        {
            "case": _case(case_id, "new_supplier_packet"),
            "routing": routing,
        }
    )

    result = quarantine_case(None, ctx)

    assert result.output["status"] == "quarantined"
    assert result.output["case_id"] == case_id
    assert get_command(db, case_id, "create_supplier") is None

    case = db.collection(CASES).document(case_id).get().to_dict()
    assert case["phase"] == "quarantined"
    assert case["routing"] == routing
    assert case["screening"] is None
    assert case["policy"] is None


def test_quarantine_case_persists_a_malformed_screening_without_raising(
    db, case_id, monkeypatch
):
    """A screening dict that app.risk.assess already rejects as
    SCREENING_MALFORMED (missing/wrong-typed id, score, or match on a
    candidate; a non-dict candidate entry) still flows into
    quarantine_case -> _record_outcome, because that is exactly the
    'blocked' terminal a malformed screening routes to. _record_outcome used
    to bracket-index c["id"]/c["score"]/c["match"], which raised
    KeyError/TypeError for the very inputs assess() already tolerates — the
    gate stopped raising, but the write recording why a case was quarantined
    did not. This proves the persistence path is total too."""
    monkeypatch.setattr(nodes_module, "get_client", lambda: db)
    db.collection(CASES).document(case_id).set({"case_id": case_id, "case_version": 1})

    routing = {
        "proposed": [],
        "route": [],
        "reason": "screening malformed",
        "refused": None,
        "pending_implementation": [],
    }
    malformed_screening = {
        "reachable": True,
        "candidates": [{"score": 0.9}, "not-a-dict", {"id": ["unhashable"]}],
        "flagged": ["y"],
    }
    ctx = _StubContext(
        {
            "case": _case(case_id, "new_supplier_packet"),
            "routing": routing,
            "screening": malformed_screening,
        }
    )

    result = quarantine_case(None, ctx)  # must not raise

    assert result.output["status"] == "quarantined"

    case = db.collection(CASES).document(case_id).get().to_dict()
    assert case["screening"]["candidate_count"] == 3
    assert case["screening"]["candidates"] == [
        {"id": None, "score": 0.9, "match": None},
        {"id": None, "score": None, "match": None},
        {"id": ["unhashable"], "score": None, "match": None},
    ]
