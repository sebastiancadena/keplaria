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
    """What the Route column shows: the engaged agents, or that a clock
    event engaged none, or nothing at all when the case has no routing."""
    routing = case.get("routing")
    if not routing:
        return {"agents": [], "clock": False}
    agents = list(routing.get("route") or [])
    clock = not agents and (case.get("event_type") in CLOCK_EVENTS)
    return {"agents": agents, "clock": clock}
