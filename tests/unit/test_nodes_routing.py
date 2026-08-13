"""Unit tests for apply_route and quarantine_case — the fail-closed gate.

apply_route must route to 'blocked' on any coordinator proposal validate_route
rejects, and reach 'screen'/'skip' only for accepted proposals — including the
legitimately empty route for an event type that requires no agents at all.
quarantine_case, the 'blocked' terminal, must never claim the create_supplier
command or call the ERP — but it does record the refusal onto the case
document, so a reviewer (and verify.py) can see why a case was blocked.
"""

from __future__ import annotations

from app.nodes import apply_route, quarantine_case
from app.state.commands import get_command
from app.state.firestore import CASES, get_client


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


def test_quarantine_case_claims_no_command_but_records_the_refusal(case_id):
    # quarantine_case resolves its own Firestore client via get_client() (same
    # as every other node), so seeding and assertions must use that client
    # too — the `db` fixture deliberately points at a separate test database
    # and would silently check the wrong place.
    db = get_client()
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
