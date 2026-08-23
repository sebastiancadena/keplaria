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
    """Exact shape, deliberately: this is an allowlist.

    `caption` and `topics` were added on 2026-08-22 -- both were already
    captured upstream in app/nodes.py and dropped here, which left the page
    rendering an opaque fixture id where a name existed. Anything ELSE
    appearing in this dict is a field that got published without being read,
    which is what this equality is here to catch.
    """
    view = public_case(RAW_CASE)
    assert view["screening"]["candidates"] == [
        {"id": "syn-co-008", "caption": None, "score": 0.526,
         "match": False, "topics": []}
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
    assert view["refused_commands"] == []


def test_refused_commands_are_emitted_with_only_action_and_cycle():
    """The claim-time refusal record must reach the public view — see
    app.nodes._claim_lifecycle_commands, which persists it onto the case
    document precisely because no outbox row exists for it. external_id and
    status carry no case detail here (status is always REFUSED_BY_DEPARTMENT)
    but the allowlist still names exactly what it emits, same as
    `commands` above."""
    case = dict(RAW_CASE, refused_commands=[
        {
            "action": "apply_hold",
            "cycle": 1,
            "status": "refused_by_department",
            "external_id": None,
        }
    ])
    view = public_case(case)
    assert view["refused_commands"] == [{"action": "apply_hold", "cycle": 1}]


def test_the_projection_carries_an_agent_policy_added_to_the_route():
    """A completed under-proposal is as much a policy action as a drop.

    The coordinator may name fewer agents than the event requires; policy
    completes the route and records the difference in `added`. Without this
    key the completion is invisible downstream and only drops are legible,
    which reads as though policy can subtract but never add.
    """
    raw = {**RAW_CASE, "routing": {**RAW_CASE["routing"],
                                   "proposed": ["compliance"],
                                   "route": ["evidence", "compliance"],
                                   "added": ["evidence"]}}
    assert public_case(raw)["routing"]["added"] == ["evidence"]


def test_the_projection_defaults_added_to_an_empty_list():
    assert public_case(RAW_CASE)["routing"]["added"] == []


# --- derived status ------------------------------------------------------
#
# `phase` is a graph-internal field and must never be shown as a status.
# commit_approval does not touch it (only app.nodes.park_case writes it), so
# an approved and executed case still reads `awaiting_approval` -- which on
# camera says a case is waiting seconds after the ERP rows appeared. The
# status below is derived from what actually happened instead.

def _case(**over) -> dict:
    base = {
        "case_id": "CASE-9",
        "case_version": 1,
        "phase": "awaiting_approval",
        "policy": {"band": "review", "score": 0.25},
    }
    base.update(over)
    return base


def test_a_parked_case_reads_as_stopped_not_as_a_phase_name():
    view = public_case(_case(), [{"action": "create_supplier", "status": "held"}])
    assert view["status"]["state"] == "PARKED"
    assert "awaiting_approval" not in view["status"]["summary"]
    assert view["status"]["erp_writes"] == 0


def test_an_applied_approval_reads_as_approved_even_though_phase_never_moved():
    """The defect this whole treatment exists for."""
    view = public_case(
        _case(approval={"decision": "approved", "case_version": 1,
                        "approval_id": "A-1"}),
        [{"action": "create_supplier", "status": "pending"}],
    )
    assert view["phase"] == "awaiting_approval"
    assert view["status"]["state"] == "APPROVED"


def test_a_case_whose_commands_all_ran_reads_as_executed():
    view = public_case(
        _case(approval={"decision": "approved", "case_version": 1,
                        "approval_id": "A-1"}),
        [{"action": "create_supplier", "status": "done"},
         {"action": "attach_evidence", "status": "done"}],
    )
    assert view["status"]["state"] == "EXECUTED"
    assert view["status"]["erp_writes"] == 2


def test_a_blocked_case_says_nothing_will_be_written():
    view = public_case(_case(policy={"band": "blocked", "score": 0.9}), [])
    assert view["status"]["state"] == "BLOCKED"


def test_a_case_still_running_reads_as_processing():
    view = public_case(_case(phase="processing", policy={}), [])
    assert view["status"]["state"] == "PROCESSING"


def test_a_superseded_approval_does_not_read_as_approved():
    """effective_band stops applying it; the status must agree with that."""
    view = public_case(
        _case(case_version=4, approval={"decision": "approved",
                                        "case_version": 1, "approval_id": "A-1"}),
        [{"action": "create_supplier", "status": "held"}],
    )
    assert view["status"]["state"] == "PARKED"


# --- screening candidates ------------------------------------------------

def test_the_projection_carries_the_candidate_name_yente_returned():
    """`caption` is captured in app/nodes.py and was dropped here.

    Without it the table shows `syn-co-008`, which tells a reader nothing
    about why the matcher hesitated -- the name is the whole story of the
    deliberate near-miss.
    """
    view = public_case(_case(screening={"candidates": [
        {"id": "syn-co-008", "caption": "Comercializadora Andes Verde S.A.S.",
         "score": 0.6716417910447763, "match": False}]}), [])
    row = view["screening"]["candidates"][0]
    assert row["caption"] == "Comercializadora Andes Verde S.A.S."


def test_candidates_are_ordered_by_score_descending():
    view = public_case(_case(screening={"candidates": [
        {"id": "a", "score": 0.17, "match": False},
        {"id": "b", "score": 0.67, "match": False},
        {"id": "c", "score": 0.04, "match": False}]}), [])
    assert [c["id"] for c in view["screening"]["candidates"]] == ["b", "a", "c"]


def test_the_projection_names_which_candidates_a_risk_factor_cited():
    """The near-match row must be markable without the template guessing.

    Marking "the highest score" would be a heuristic that silently lies the
    moment a factor cites something else. The factor's own value names the
    candidate, so the projection reports what was cited rather than what
    looks likely.
    """
    view = public_case(_case(
        policy={"band": "review", "score": 0.25, "factors_fired": [
            {"id": "SUBTHRESHOLD_CANDIDATE", "value": "syn-co-008 @ 0.672",
             "weight": 0.25}]},
        screening={"candidates": [
            {"id": "syn-co-008", "score": 0.67, "match": False},
            {"id": "syn-co-006", "score": 0.18, "match": False}]}), [])
    assert view["cited_candidate_ids"] == ["syn-co-008"]


def test_no_factors_cites_no_candidates():
    view = public_case(_case(screening={"candidates": [
        {"id": "syn-co-006", "score": 0.18, "match": False}]}), [])
    assert view["cited_candidate_ids"] == []


def test_a_non_string_candidate_id_does_not_take_the_page_down():
    """Producer-supplied and therefore untrusted.

    Found by an existing fixture, not by inspection: the first version of
    the citation lookup did `id in values` and raised TypeError on a list,
    which would have been a 500 on the public console rather than one
    unmarked row.
    """
    view = public_case(_case(screening={"candidates": [
        {"id": ["syn-co-008"], "score": 0.67, "match": False}]}), [])
    assert view["cited_candidate_ids"] == []


# --- lifecycle position --------------------------------------------------

def test_lifecycle_defaults_to_onboarding():
    got = public_case({"case_id": "C1"})
    assert got["lifecycle"]["step"] == "onboarding"
    assert [s["key"] for s in got["lifecycle"]["steps"]] == [
        "onboarding", "active", "renewal_requested", "held", "released"]


def test_lifecycle_reads_the_persisted_state():
    got = public_case({"case_id": "C1", "lifecycle": {"state": "held"}})
    assert got["lifecycle"]["step"] == "held"
    current = [s["key"] for s in got["lifecycle"]["steps"] if s["current"]]
    assert current == ["held"]


def test_an_active_case_with_a_cleared_hold_reads_as_released():
    got = public_case(
        {"case_id": "C1", "lifecycle": {"state": "active"}},
        [{"action": "clear_hold", "status": "done", "cycle": 3}])
    assert got["lifecycle"]["step"] == "released"


def test_an_active_case_with_no_cleared_hold_reads_as_active():
    got = public_case({"case_id": "C1", "lifecycle": {"state": "active"}})
    assert got["lifecycle"]["step"] == "active"


def test_a_quarantined_case_marks_no_step_current():
    got = public_case({"case_id": "C1", "lifecycle": {"state": "quarantined"}})
    assert got["lifecycle"]["quarantined"] is True
    assert got["lifecycle"]["step"] is None
    assert not [s for s in got["lifecycle"]["steps"] if s["current"]]


def test_an_unknown_lifecycle_state_does_not_raise():
    got = public_case({"case_id": "C1", "lifecycle": {"state": "wat"}})
    assert got["lifecycle"]["step"] is None
