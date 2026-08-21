"""The public projection is an allowlist, and this file is what pins that.

A blacklist test ("the endpoint is absent") passes for a projection that
forgot to filter a field nobody thought of. The unknown-field case below is
the one that actually enforces the guarantee.
"""

from __future__ import annotations

from console.projection import public_case

RAW_CASE = {
    "case_id": "CASE-1",
    "case_version": 4,
    "phase": "awaiting_approval",
    "supplier": "Andes Foods",
    "routing": {
        "proposed": ["compliance"],
        "route": ["compliance"],
        "dropped": [],
        "reason": "New supplier packet requires screening.",
        "refused": None,
        "department": "procurement",
        "department_source": "event",
        "evidence_skipped_no_document": False,
        "evidence_skipped_tainted_document": False,
    },
    "screening": {
        "reachable": True,
        "endpoint": "http://10.10.0.2:8000",
        "flagged": [],
        "candidate_count": 1,
        "candidates": [{"id": "syn-co-008", "score": 0.526, "match": False}],
    },
    "policy": {
        "policy_id": "supplier_risk",
        "policy_version": 2,
        "score": 0.25,
        "band": "review",
        "factors_fired": ["SUBTHRESHOLD_CANDIDATE"],
        "reasons": ["candidate below flag threshold"],
    },
    "injection": {
        "tainted": False,
        "finding_count": 0,
        "findings": [{"pattern_id": "P1", "page": 0, "offset": 812}],
    },
    "compliance": {"note": "not surveyed for public display"},
    "approval": {
        "approval_id": "CASE-1:v4",
        "decision": "approved",
        "actor": "reviewer@example.com",
        "case_version": 4,
    },
}


def test_an_unknown_field_is_never_emitted():
    """The allowlist guarantee, stated as a test.

    If this fails, someone switched public_case to a blacklist and a future
    field will leak the day it is added.
    """
    view = public_case(dict(RAW_CASE, secret_internal_note="do not publish"))
    assert "secret_internal_note" not in view


def test_the_screening_endpoint_is_withheld():
    view = public_case(RAW_CASE)
    assert "endpoint" not in view["screening"]
    assert "10.10.0.2" not in repr(view)


def test_the_approval_actor_is_withheld():
    view = public_case(RAW_CASE)
    assert "reviewer@example.com" not in repr(view)
    assert view["approval"]["decision"] == "approved"


def test_injection_offsets_are_withheld_but_the_verdict_is_kept():
    view = public_case(RAW_CASE)
    assert view["injection"] == {"tainted": False, "finding_count": 0}


def test_the_compliance_block_is_withheld():
    view = public_case(RAW_CASE)
    assert "compliance" not in view


def test_both_bands_are_emitted_and_can_differ():
    """An applying approval changes the effective band and never the gate's."""
    view = public_case(RAW_CASE)
    assert view["gate_band"] == "review"
    assert view["effective_band"] == "clear"


def test_a_superseded_approval_stops_applying():
    stale = dict(RAW_CASE, case_version=5)
    view = public_case(stale)
    assert view["gate_band"] == "review"
    assert view["effective_band"] == "review"
    assert view["approval"]["applies"] is False


def test_subthreshold_candidates_are_emitted():
    view = public_case(RAW_CASE)
    assert view["screening"]["candidates"] == [
        {"id": "syn-co-008", "score": 0.526, "match": False}
    ]


def test_commands_emit_only_action_status_and_cycle():
    """A command's payload carries case detail and must never reach the page."""
    view = public_case(RAW_CASE, commands=[{
        "action": "create_supplier",
        "status": "pending",
        "cycle": 1,
        "payload": {"supplier": "Andes Foods", "bank_account": "0001234567"},
        "error": None,
    }])
    assert view["commands"] == [
        {"action": "create_supplier", "status": "pending", "cycle": 1}
    ]
    assert "0001234567" not in repr(view)


def test_the_projection_emits_the_routing_department_fields():
    projected = public_case(RAW_CASE)
    assert projected["routing"]["department"] == "procurement"
    assert projected["routing"]["department_source"] == "event"


def test_a_case_with_no_optional_blocks_does_not_raise():
    view = public_case({"case_id": "CASE-2", "case_version": 1, "phase": "processing"})
    assert view["case_id"] == "CASE-2"
    assert view["approval"] is None
    assert view["screening"]["candidates"] == []
