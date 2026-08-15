"""Unit tests for the compliance interpretation path: schema, apply_compliance's
grounding check, assess_risk's one-way escalation, screen_supplier's routing
into the interpreter, and park_case persisting the compliance block."""

from __future__ import annotations

import json

import pytest

import app.nodes as nodes_module
from app.nodes import apply_compliance, assess_risk, park_case, screen_supplier
from app.schemas import ComplianceAssessment
from app.state.firestore import CASES


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


def _case(case_id: str) -> dict:
    return {"case_id": case_id, "event_type": "new_supplier_packet", "supplier": "Acme"}


def _compliance(valid=True, recommendation="note_clear") -> dict:
    record = _assessment(recommendation=recommendation)
    record["valid"] = valid
    if not valid:
        record["invalid_reason"] = "UNKNOWN_CANDIDATE_ID"
    return record


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _yente_payload(candidates):
    return {"responses": {"q": {"results": candidates}}}


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


def test_apply_compliance_handles_non_list_candidates_without_raising():
    screening = _screening_state()
    screening["candidates"] = 1
    ctx = _StubContext({"case": {"case_id": "TEST-AC-5"}, "screening": screening})

    result = apply_compliance(_assessment(), ctx)

    assert result.output["valid"] is False
    assert result.output["invalid_reason"] == "UNKNOWN_CANDIDATE_ID"


def test_apply_compliance_handles_a_non_dict_screening_without_raising():
    """screening is normally a dict written by screen_supplier, but ctx.state
    is a plain session-state bag with no schema of its own — a state store
    can hand back a JSON string or any other truthy non-dict value. app.risk
    .assess already guards this exact shape explicitly; apply_compliance must
    never raise on it either, since the engine allows only one concurrent
    query and a raising node becomes retry pressure instead of a decision."""
    ctx = _StubContext(
        {"case": {"case_id": "TEST-AC-6"}, "screening": "not-a-dict"}
    )

    result = apply_compliance(_assessment(), ctx)

    assert result.output["valid"] is False
    assert result.output["invalid_reason"] == "UNKNOWN_CANDIDATE_ID"


def test_escalate_review_tightens_clear_to_review(case_id):
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening_state(),  # low scores, no match -> clear
            "compliance": _compliance(recommendation="escalate_review"),
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "review"
    assert "COMPLIANCE_ESCALATION" in result.output["reasons"]


def test_invalid_assessment_tightens_clear_to_review(case_id):
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening_state(),
            "compliance": _compliance(valid=False),
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "review"
    assert "COMPLIANCE_ASSESSMENT_INVALID" in result.output["reasons"]


def test_note_clear_leaves_clear_untouched(case_id):
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening_state(),
            "compliance": _compliance(recommendation="note_clear"),
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "clear"
    assert "COMPLIANCE_ESCALATION" not in result.output["reasons"]


@pytest.mark.parametrize(
    "compliance",
    [
        _compliance(recommendation="note_clear"),
        _compliance(recommendation="escalate_review"),
        _compliance(valid=False),
    ],
    ids=["note_clear", "escalate_review", "invalid_record"],
)
def test_blocked_is_never_downgraded_by_the_agent(case_id, compliance):
    """The escalation is one-way: it may only tighten a fresh clear verdict.
    A blocked verdict must survive every shape the agent's output can take —
    a clean recommendation, an escalating one, and an invalid record — since
    a guard as narrow as `if verdict.band != BLOCKED` would still pass a
    single-case version of this test but fail two of these three."""
    screening = _screening_state()
    screening["candidates"][0].update({"score": 1.0, "match": True})
    screening["flagged"] = [screening["candidates"][0]["id"]]
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": screening,
            "compliance": compliance,
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "blocked"


def test_review_band_is_not_double_modified_by_escalation(case_id):
    screening = _screening_state()
    screening["candidates"][0]["score"] = 0.55  # sub-threshold factor -> review
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": screening,
            "compliance": _compliance(recommendation="escalate_review"),
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "review"
    assert "COMPLIANCE_ESCALATION" not in result.output["reasons"]


def test_carry_forward_path_ignores_a_compliance_block(case_id):
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": None,
            "case_state": {
                "policy": {
                    "policy_id": "supplier_risk",
                    "policy_version": 1,
                    "score": 0.0,
                    "band": "clear",
                    "factors_fired": [],
                    "reasons": [],
                }
            },
            "compliance": _compliance(recommendation="escalate_review"),
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "clear"


def test_screen_supplier_routes_candidates_to_the_interpreter(case_id, monkeypatch):
    candidates = [
        {"id": "c-1", "caption": "Acme Holdings", "score": 0.4, "match": False,
         "properties": {"topics": ["sanction"]}}
    ]
    monkeypatch.setattr(
        nodes_module.httpx, "post", lambda *a, **k: _FakeResponse(_yente_payload(candidates))
    )
    ctx = _StubContext({"case": _case(case_id)})

    result = screen_supplier(None, ctx)

    assert result.actions.route == "interpret"
    delta = result.actions.state_delta
    assert delta["screening_supplier_name"] == "Acme"
    parsed = json.loads(delta["screening_candidates"])
    assert parsed[0]["id"] == "c-1"


def test_screen_supplier_skips_the_interpreter_when_no_candidates(case_id, monkeypatch):
    monkeypatch.setattr(
        nodes_module.httpx, "post", lambda *a, **k: _FakeResponse(_yente_payload([]))
    )
    ctx = _StubContext({"case": _case(case_id)})

    result = screen_supplier(None, ctx)

    assert result.actions.route == "score"
    assert result.actions.state_delta["screening_candidates"] == "[]"


def test_screen_supplier_skips_the_interpreter_when_unreachable(case_id, monkeypatch):
    def _boom(*a, **k):
        raise nodes_module.httpx.ConnectError("no route")

    monkeypatch.setattr(nodes_module.httpx, "post", _boom)
    ctx = _StubContext({"case": _case(case_id)})

    result = screen_supplier(None, ctx)

    assert result.actions.route == "score"
    assert result.actions.state_delta["screening"]["reachable"] is False


def test_park_case_persists_the_compliance_block(db, case_id):
    compliance = _compliance(recommendation="escalate_review")
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening_state(),
            "compliance": compliance,
        }
    )

    park_case(None, ctx)

    doc = db.collection(CASES).document(case_id).get().to_dict()
    assert doc["compliance"] == compliance
