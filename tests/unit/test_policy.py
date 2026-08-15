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


def test_a_disallowed_agent_is_dropped_not_refused():
    """A known agent the event type doesn't permit narrows the route rather
    than refusing the whole proposal — the user's decision after the
    coordinator was observed live, reproducibly, over-proposing `compliance`
    for `certificate_received` despite its own instruction saying not to.
    Before this change this raised PolicyError (see git history for
    `test_route_outside_the_allowlist_is_refused`, which this replaces); now
    the disallowed agent is silently absent from the returned route."""
    assert validate_route("certificate_received", ["compliance"]) == []


def test_over_proposing_a_disallowed_agent_still_returns_the_permitted_ones():
    assert validate_route("certificate_received", ["evidence", "compliance"]) == ["evidence"]


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
    # A clock event's allowed set is empty, so any proposed agent is
    # necessarily "known but not permitted here" — narrowed to [], not
    # refused, same as any other over-proposal.
    assert validate_route("evidence_overdue", ["evidence"]) == []


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
    """"May not" now means the agent is dropped and never runs, not that
    proposing one raises — no agent reaches ctx.state["routing"]["route"]
    for a clock event either way, which is the guarantee that actually
    matters here."""
    assert validate_route("renewal_due", ["compliance"]) == []
