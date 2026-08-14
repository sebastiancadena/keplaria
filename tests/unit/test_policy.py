"""Routing is proposed by a model and validated by code.

The model never gets the last word on which agents may run for an event type.
"""

import pytest

from app.policy import ALLOWED_ROUTES, PolicyError, validate_route


def test_permitted_route_is_returned_normalised():
    assert validate_route("new_supplier_packet", ["compliance", "evidence"]) == [
        "evidence",
        "compliance",
    ]


def test_route_outside_the_allowlist_is_refused():
    with pytest.raises(PolicyError, match="not permitted"):
        validate_route("certificate_received", ["compliance"])


def test_unknown_agent_name_is_refused():
    with pytest.raises(PolicyError, match="unknown agent"):
        validate_route("new_supplier_packet", ["finance_bot"])


def test_unknown_event_type_is_refused():
    with pytest.raises(PolicyError, match="unknown event type"):
        validate_route("wire_transfer", ["evidence"])


def test_empty_route_is_refused_when_the_event_type_requires_work():
    with pytest.raises(PolicyError, match="empty route"):
        validate_route("new_supplier_packet", [])


def test_deterministic_event_type_permits_no_agents():
    assert validate_route("evidence_overdue", []) == []
    with pytest.raises(PolicyError, match="not permitted"):
        validate_route("evidence_overdue", ["evidence"])


def test_duplicate_entries_are_collapsed():
    assert validate_route("certificate_received", ["evidence", "evidence"]) == ["evidence"]


def test_allowlist_covers_every_supported_event_type():
    assert set(ALLOWED_ROUTES) == {
        "new_supplier_packet",
        "certificate_received",
        "evidence_overdue",
        "renewal_due",
    }


def test_clock_events_engage_no_agents():
    from app.policy import CLOCK_EVENTS
    assert ALLOWED_ROUTES["renewal_due"] == set()
    assert ALLOWED_ROUTES["evidence_overdue"] == set()
    assert validate_route("renewal_due", []) == []


def test_clock_events_are_classified_as_such():
    from app.policy import CLOCK_EVENTS
    assert CLOCK_EVENTS == frozenset({"renewal_due", "evidence_overdue"})
    assert "new_supplier_packet" not in CLOCK_EVENTS
    assert "certificate_received" not in CLOCK_EVENTS


def test_a_clock_event_may_not_engage_an_agent():
    from app.policy import CLOCK_EVENTS
    with pytest.raises(PolicyError):
        validate_route("renewal_due", ["compliance"])
