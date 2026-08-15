"""The transition rules. Pure — no Firestore, no engine, no ERP."""

from app.lifecycle import (
    ACTIVE,
    APPLY_HOLD,
    ATTACH_EVIDENCE,
    CLEAR_HOLD,
    CREATE_SUPPLIER,
    HELD,
    ONBOARDING,
    RENEWAL_REQUESTED,
    decide,
)
from app.risk import LifecycleTiming

TIMING = LifecycleTiming(renewal_window_days=35, overdue_grace_days=0)
TIMING_WITH_GRACE = LifecycleTiming(renewal_window_days=35, overdue_grace_days=5)


def _case(state, cycle=1, expiry="2027-01-01", evidence_version=1):
    return {
        "case_id": "CASE-1",
        "supplier": "Comercializadora Andes Verde SAS",
        "lifecycle": {"state": state, "cycle": cycle},
        "certificate": {"expiry_date": expiry, "evidence_version": evidence_version},
    }


def _event(event_type, effective_date):
    return {
        "event_type": event_type,
        "case_id": "CASE-1",
        "supplier": "Comercializadora Andes Verde SAS",
        "effective_date": effective_date,
    }


def _evidence(expiry):
    return {
        "document_checksum": "abc123",
        "fields": [{"name": "certificate_expiry", "value": expiry, "page": 0,
                    "span": f"Expiry: {expiry}", "confidence": 0.98}],
    }


def _actions(decision):
    return [c.action for c in decision.commands]


def test_onboarding_creates_the_supplier_and_attaches_evidence():
    case = {"case_id": "CASE-1", "supplier": "Andes"}

    decision = decide(
        case_state=case,
        event=_event("new_supplier_packet", "2026-01-01"),
        evidence=_evidence("2027-01-01"),
        timing=TIMING,
    )

    assert _actions(decision) == [CREATE_SUPPLIER, ATTACH_EVIDENCE]
    assert decision.state == ACTIVE
    assert decision.cycle == 1
    assert decision.certificate["expiry_date"] == "2027-01-01"


def test_renewal_inside_the_window_is_requested():
    decision = decide(
        case_state=_case(ACTIVE, expiry="2027-01-01"),
        event=_event("renewal_due", "2026-12-01"),
        evidence=None,
        timing=TIMING,
    )

    assert _actions(decision) == ["request_renewal"]
    assert decision.state == RENEWAL_REQUESTED


def test_renewal_before_the_window_opens_is_not_due():
    decision = decide(
        case_state=_case(ACTIVE, expiry="2027-01-01"),
        event=_event("renewal_due", "2026-10-01"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "NOT_DUE"
    assert decision.state == ACTIVE


def test_the_window_boundary_is_inclusive():
    # 2027-01-01 minus 35 days is 2026-11-27; on that exact day it is due.
    decision = decide(
        case_state=_case(ACTIVE, expiry="2027-01-01"),
        event=_event("renewal_due", "2026-11-27"),
        evidence=None,
        timing=TIMING,
    )

    assert _actions(decision) == ["request_renewal"]


def test_a_second_renewal_in_the_same_cycle_is_refused():
    decision = decide(
        case_state=_case(RENEWAL_REQUESTED),
        event=_event("renewal_due", "2026-12-05"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "ALREADY_REQUESTED"


def test_overdue_past_expiry_applies_a_hold():
    decision = decide(
        case_state=_case(RENEWAL_REQUESTED, expiry="2027-01-01"),
        event=_event("evidence_overdue", "2027-01-15"),
        evidence=None,
        timing=TIMING,
    )

    assert _actions(decision) == [APPLY_HOLD]
    assert decision.state == HELD


def test_overdue_before_expiry_is_not_due():
    decision = decide(
        case_state=_case(RENEWAL_REQUESTED, expiry="2027-01-01"),
        event=_event("evidence_overdue", "2026-12-20"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "NOT_DUE"


def test_overdue_after_the_certificate_already_arrived_is_superseded():
    decision = decide(
        case_state=_case(ACTIVE, cycle=2, expiry="2028-01-01"),
        event=_event("evidence_overdue", "2027-01-15"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "SUPERSEDED", (
        "a late overdue event must never hold a supplier who already renewed"
    )


def test_overdue_when_a_renewal_was_never_requested_is_not_applicable():
    """ONBOARDING is the state renewal_due's analogous non-ACTIVE branch
    already calls NOT_APPLICABLE — nothing was superseded here because no
    renewal was ever requested for this case, so the two clock branches must
    agree on vocabulary for 'this event has nothing to do' rather than one of
    them claiming a supersession that never happened."""
    decision = decide(
        case_state=_case(ONBOARDING),
        event=_event("evidence_overdue", "2027-01-15"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "NOT_APPLICABLE"


def test_a_renewed_certificate_advances_the_cycle_and_releases_the_hold():
    decision = decide(
        case_state=_case(HELD, cycle=1, expiry="2027-01-01"),
        event=_event("certificate_received", "2027-01-20"),
        evidence=_evidence("2028-01-01"),
        timing=TIMING,
    )

    assert _actions(decision) == [ATTACH_EVIDENCE, CLEAR_HOLD]
    assert decision.state == ACTIVE
    assert decision.cycle == 2
    assert decision.certificate["expiry_date"] == "2028-01-01"
    assert decision.certificate["evidence_version"] == 2


def test_a_certificate_that_does_not_extend_expiry_is_stale():
    decision = decide(
        case_state=_case(HELD, expiry="2027-01-01"),
        event=_event("certificate_received", "2027-01-20"),
        evidence=_evidence("2026-06-01"),
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "STALE_DOCUMENT"
    assert decision.state == HELD, "a stale document must not release a hold"


def test_an_unknown_event_type_yields_no_commands():
    decision = decide(
        case_state=_case(ACTIVE),
        event=_event("something_else", "2027-01-20"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "UNKNOWN_EVENT_TYPE"


def test_decide_never_raises_on_a_malformed_effective_date():
    decision = decide(
        case_state=_case(ACTIVE),
        event=_event("renewal_due", "not-a-date"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "BAD_EFFECTIVE_DATE"


def test_overdue_hold_respects_a_nonzero_grace_period():
    # expiry 2027-01-01 + a 5-day grace: the boundary day (2027-01-06) is
    # still within grace and must not fire; the day after (2027-01-07) must.
    case = _case(RENEWAL_REQUESTED, expiry="2027-01-01")

    boundary = decide(
        case_state=case,
        event=_event("evidence_overdue", "2027-01-06"),
        evidence=None,
        timing=TIMING_WITH_GRACE,
    )
    assert boundary.commands == []
    assert boundary.reason == "NOT_DUE"

    day_after = decide(
        case_state=case,
        event=_event("evidence_overdue", "2027-01-07"),
        evidence=None,
        timing=TIMING_WITH_GRACE,
    )
    assert _actions(day_after) == [APPLY_HOLD]
    assert day_after.state == HELD


def test_a_non_numeric_cycle_does_not_raise():
    case = _case(ACTIVE, expiry="2027-01-01")
    case["lifecycle"]["cycle"] = "not-a-number"

    decision = decide(
        case_state=case,
        event=_event("renewal_due", "2026-10-01"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "NOT_DUE"


def test_a_non_numeric_evidence_version_does_not_raise():
    case = _case(HELD, expiry="2027-01-01")
    case["certificate"]["evidence_version"] = "bogus"

    decision = decide(
        case_state=case,
        event=_event("certificate_received", "2027-01-20"),
        evidence=_evidence("2026-06-01"),
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "STALE_DOCUMENT"


def test_a_non_dict_lifecycle_does_not_raise():
    case = {"case_id": "CASE-1", "supplier": "Andes", "lifecycle": ["bad"]}

    decision = decide(
        case_state=case,
        event=_event("something_else", "2027-01-20"),
        evidence=None,
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "UNKNOWN_EVENT_TYPE"


def test_a_non_dict_evidence_does_not_raise():
    decision = decide(
        case_state=_case(ACTIVE, expiry="2027-01-01"),
        event=_event("certificate_received", "2027-01-20"),
        evidence=["not", "a", "dict"],
        timing=TIMING,
    )

    assert decision.commands == []
    assert decision.reason == "NO_EXPIRY_FOUND"
