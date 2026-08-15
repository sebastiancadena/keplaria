"""Unit tests for the compliance assessment path: schema, validator, escalation."""

from __future__ import annotations

from app.schemas import CandidateAssessment, ComplianceAssessment


def test_compliance_assessment_parses_a_complete_payload():
    parsed = ComplianceAssessment(
        assessments=[
            {"candidate_id": "c-1", "relevant": True, "reasoning": "same name"}
        ],
        recommendation="escalate_review",
        rationale="one plausible candidate",
    )
    assert parsed.assessments[0].candidate_id == "c-1"
    assert parsed.recommendation == "escalate_review"


from app.nodes import apply_compliance


class _StubContext:
    """The nodes under test only read ctx.state — a dict wrapper is enough.

    Same shape as the stub in tests/unit/test_nodes_risk.py.
    """

    def __init__(self, state: dict):
        self.state = state


def _screening_state(candidate_ids=("c-1", "c-2")) -> dict:
    return {
        "endpoint": "http://10.10.0.2:8000",
        "supplier": "Acme",
        "reachable": True,
        "candidates": [
            {"id": cid, "caption": f"Entity {cid}", "score": 0.4, "match": False,
             "topics": ["sanction"]}
            for cid in candidate_ids
        ],
        "flagged": [],
        "error": None,
    }


def _assessment(candidate_id="c-1", recommendation="note_clear") -> dict:
    return {
        "assessments": [
            {"candidate_id": candidate_id, "relevant": False, "reasoning": "different entity"}
        ],
        "recommendation": recommendation,
        "rationale": "no plausible connection",
    }


def test_apply_compliance_accepts_a_grounded_assessment():
    ctx = _StubContext({"case": {"case_id": "TEST-AC-1"}, "screening": _screening_state()})

    result = apply_compliance(_assessment(), ctx)

    assert result.output["valid"] is True
    # Event.state lands in actions.state_delta — that is what ADK merges
    # into session state for downstream nodes.
    assert result.actions.state_delta["compliance"]["valid"] is True
    assert result.actions.state_delta["compliance"]["recommendation"] == "note_clear"


def test_apply_compliance_rejects_an_invented_candidate_id():
    ctx = _StubContext({"case": {"case_id": "TEST-AC-2"}, "screening": _screening_state()})

    result = apply_compliance(_assessment(candidate_id="made-up"), ctx)

    assert result.output["valid"] is False
    assert result.output["invalid_reason"] == "UNKNOWN_CANDIDATE_ID"


def test_apply_compliance_rejects_an_unknown_recommendation():
    ctx = _StubContext({"case": {"case_id": "TEST-AC-3"}, "screening": _screening_state()})

    result = apply_compliance(_assessment(recommendation="approve_now"), ctx)

    assert result.output["valid"] is False
    assert result.output["invalid_reason"] == "BAD_RECOMMENDATION"


def test_apply_compliance_rejects_unparseable_input():
    ctx = _StubContext({"case": {"case_id": "TEST-AC-4"}, "screening": _screening_state()})

    result = apply_compliance("not json at all {", ctx)

    assert result.output["valid"] is False
    assert result.output["invalid_reason"] == "UNPARSEABLE"
    assert result.actions.state_delta["compliance"]["valid"] is False
