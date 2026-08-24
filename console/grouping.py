"""Group the public case list by supplier, for the console home.

The list is deployed-state evidence for gates (a ten-run streak leaves ten
cases of one supplier at one phase), so it is legitimately repetitive. The
README's thesis is "one durable mission per supplier"; a flat list of 29
rows for 6 suppliers reads as the opposite. Grouping restores the shape
without hiding a row.
"""

from __future__ import annotations

from app.catalog import CLOCK_EVENTS

NO_SUPPLIER = "(no supplier)"


def _label_for(case: dict) -> str | None:
    lifecycle = case.get("lifecycle") or {}
    for step in lifecycle.get("steps") or []:
        if step.get("current"):
            return step.get("label")
    return None


def group_by_supplier(cases: list[dict]) -> list[dict]:
    """Heading rows in first-appearance order, so today's ordering (parked
    first) still decides which supplier is at the top."""
    groups: dict[str, dict] = {}
    for case in cases:
        name = case.get("supplier") or NO_SUPPLIER
        group = groups.setdefault(name, {"supplier": name, "cases": []})
        group["cases"].append(case)
    for group in groups.values():
        group["count"] = len(group["cases"])
        newest = max(group["cases"], key=lambda c: str(c.get("updated_at") or ""))
        group["step"] = _label_for(newest)
    return list(groups.values())


def route_label(case: dict) -> dict:
    """What the Route column shows: the engaged agents, or that the case's
    latest claimed event is a clock event, or nothing at all.

    The clock check comes first and short-circuits the routing block: when
    the latest event is a clock event, `routing` on the document may still
    be a stale block left over from an earlier (non-clock) event, and that
    stale route is not what the latest event did. Only when the latest
    event is not a clock event do the routing block's agents apply.
    """
    if case.get("event_type") in CLOCK_EVENTS:
        return {"agents": [], "clock": True}
    routing = case.get("routing")
    if not routing:
        return {"agents": [], "clock": False}
    return {"agents": list(routing.get("route") or []), "clock": False}
