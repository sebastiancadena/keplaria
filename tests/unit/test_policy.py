"""Routing is proposed by a model and validated by code.

The model never gets the last word on which agents may run for an event type.
"""

import pytest

from app.policy import PolicyError, allowed_routes, validate_route


def test_permitted_route_is_returned_normalised():
    assert validate_route(
        "new_supplier_packet", ["compliance", "evidence"], "procurement"
    ) == [
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
    assert validate_route("certificate_received", ["compliance"], "procurement") == []


def test_over_proposing_a_disallowed_agent_still_returns_the_permitted_ones():
    assert validate_route(
        "certificate_received", ["evidence", "compliance"], "procurement"
    ) == ["evidence"]


def test_unknown_agent_name_is_refused():
    with pytest.raises(PolicyError, match="UNKNOWN_AGENT"):
        validate_route("new_supplier_packet", ["finance_bot"], "procurement")


def test_unknown_event_type_is_refused():
    with pytest.raises(PolicyError, match="UNKNOWN_EVENT_TYPE"):
        validate_route("wire_transfer", ["evidence"], "procurement")


def test_empty_route_is_refused_when_the_event_type_requires_work():
    with pytest.raises(PolicyError, match="EMPTY_ROUTE"):
        validate_route("new_supplier_packet", [], "procurement")


def test_deterministic_event_type_permits_no_agents():
    assert validate_route("evidence_overdue", [], "procurement") == []
    # A clock event's allowed set is empty, so any proposed agent is
    # necessarily "known but not permitted here" — narrowed to [], not
    # refused, same as any other over-proposal.
    assert validate_route("evidence_overdue", ["evidence"], "procurement") == []


def test_duplicate_entries_are_collapsed():
    assert validate_route(
        "certificate_received", ["evidence", "evidence"], "procurement"
    ) == ["evidence"]


def test_allowlist_covers_every_supported_event_type():
    assert set(allowed_routes()) == {
        "new_supplier_packet",
        "certificate_received",
        "evidence_overdue",
        "renewal_due",
    }


def test_clock_events_engage_no_agents():
    assert allowed_routes()["renewal_due"] == set()
    assert allowed_routes()["evidence_overdue"] == set()
    assert validate_route("renewal_due", [], "procurement") == []


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
    assert validate_route("renewal_due", ["compliance"], "procurement") == []


def test_the_catalog_route_map_matches_the_expected_allowlist():
    """Spelled as literals, deliberately — comparing the derivation to the
    artifact it derives from would only prove the code ran."""
    assert allowed_routes() == {
        "new_supplier_packet": {"evidence", "compliance"},
        "certificate_received": {"evidence"},
        "evidence_overdue": set(),
        "renewal_due": set(),
    }


def test_a_cross_department_agent_is_refused():
    """A reach for a capability the calling department was never granted is
    an authorization violation and must be VISIBLE — refused, not narrowed.
    Phrased per the boundary rule: the finance-labeled event is refused and
    recorded; nothing here claims finance is prevented from anything."""
    with pytest.raises(PolicyError, match="DEPARTMENT_FORBIDS_AGENT"):
        validate_route("new_supplier_packet", ["evidence"], "finance")


def test_department_refusal_precedes_event_type_drop():
    """Ordering is load-bearing: compliance is disallowed for
    certificate_received (a drop, were it in scope) AND outside finance's
    scope (a refusal). Refuse must win, or the cross-department reach
    vanishes into a `dropped` diff nobody alerts on."""
    with pytest.raises(PolicyError, match="DEPARTMENT_FORBIDS_AGENT"):
        validate_route("certificate_received", ["compliance"], "finance")


def test_intra_department_over_proposal_still_drops():
    """The pre-department behaviour, byte for byte: a known agent the event
    type doesn't permit, proposed within the caller's own scope, narrows
    silently."""
    assert validate_route(
        "certificate_received", ["evidence", "compliance"], "procurement"
    ) == ["evidence"]


def test_an_unknown_department_is_refused():
    with pytest.raises(PolicyError, match="UNKNOWN_DEPARTMENT"):
        validate_route("new_supplier_packet", ["evidence"], "warehouse")


def test_resolve_department_prefers_the_event_value():
    from app.policy import resolve_department
    assert resolve_department("compliance") == ("compliance", "event")


def test_resolve_department_falls_back_to_the_catalog_legacy_value():
    from app.policy import resolve_department
    assert resolve_department(None) == ("procurement", "legacy_default")


def test_a_null_legacy_department_refuses_a_v1_event(tmp_path, monkeypatch):
    """Sunsetting the grandfather clause is a catalog edit, and the refusal
    it produces must be the coded one."""
    import copy
    import json

    import app.catalog as catalog_module
    from tests.unit.test_catalog import _catalog_dict
    from app.policy import resolve_department

    catalog = copy.deepcopy(_catalog_dict())
    catalog["legacy"]["v1_department"] = None
    path = tmp_path / "fleet.test.json"
    path.write_text(json.dumps(catalog))
    monkeypatch.setattr(catalog_module, "DEFAULT_CATALOG_PATH", path)
    catalog_module.reset_catalog_cache()
    try:
        with pytest.raises(PolicyError, match="UNKNOWN_DEPARTMENT"):
            resolve_department(None)
    finally:
        catalog_module.reset_catalog_cache()
