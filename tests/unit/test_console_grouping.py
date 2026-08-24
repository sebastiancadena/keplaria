"""Grouping is pure: it takes public_case dicts and returns heading rows."""

from __future__ import annotations

from console.grouping import group_by_supplier, route_label


def _case(cid, supplier, step, updated, route=None, event="new_supplier_packet"):
    return {
        "case_id": cid, "supplier": supplier, "updated_at": updated,
        "event_type": event,
        "lifecycle": {"step": step, "steps": [
            {"key": step, "label": step.replace("_", " ").title(), "current": True}
        ] if step else []},
        "routing": {"route": route} if route is not None else None,
    }


def test_groups_keep_first_appearance_order_and_count():
    groups = group_by_supplier([
        _case("A1", "Andes", "active", "2026-08-02"),
        _case("B1", "Llanos", "held", "2026-08-03"),
        _case("A2", "Andes", "onboarding", "2026-08-01"),
    ])
    assert [g["supplier"] for g in groups] == ["Andes", "Llanos"]
    assert groups[0]["count"] == 2
    assert [c["case_id"] for c in groups[0]["cases"]] == ["A1", "A2"]


def test_the_heading_step_comes_from_the_most_recently_updated_case():
    groups = group_by_supplier([
        _case("A1", "Andes", "onboarding", "2026-08-01"),
        _case("A2", "Andes", "held", "2026-08-09"),
        _case("A3", "Andes", "active", "2026-08-05"),
    ])
    assert groups[0]["step"] == "Held"


def test_a_case_without_a_supplier_still_groups():
    groups = group_by_supplier([_case("X", None, None, "2026-08-01")])
    assert groups[0]["supplier"] == "(no supplier)"
    assert groups[0]["step"] is None


def test_route_label_distinguishes_agents_clock_and_absent():
    assert route_label(_case("A", "s", "active", "", route=["evidence", "compliance"])) == {
        "agents": ["evidence", "compliance"], "clock": False}
    # A stale routing block (non-empty route, left over from an earlier
    # onboarding event) must not leak agents when the LATEST claimed event
    # is a clock event -- that routing block is not what the clock event did.
    assert route_label(_case(
        "B", "s", "active", "", route=["evidence", "compliance"], event="renewal_due"
    )) == {"agents": [], "clock": True}
    assert route_label(_case("C", "s", "active", "")) == {"agents": [], "clock": False}
