"""How often each cell of the fleet's scope matrix was actually exercised.

The matrix on /fleet is the rulebook. A rulebook nobody has ever tested reads
as a claim; the same rulebook with "finance: 0 engaged, 2 refused" beside it
reads as evidence. Counts are over the cases the console lists -- the same
bounded set as the home page -- and the template states that population.
Pure: case documents, outbox rows, and inbox rows in, numbers out.

Event types live in each case's `inbox` subcollection, never on the case
document (see console.store.load_inbox), so `events_by_case` carries them in
rather than this module reading `case["event_type"]`.
"""

from __future__ import annotations

from app.catalog import COMMAND_ORDER, KNOWN_COMMANDS


def command_columns() -> list[str]:
    """Every command the lifecycle can issue, in lifecycle order, extras
    sorted after. The one place this list is built -- console.public's
    fleet view imports it rather than recomputing the same expression."""
    return list(COMMAND_ORDER) + sorted(KNOWN_COMMANDS - set(COMMAND_ORDER))


def exercise_counts(
    catalog,
    cases: list[dict],
    commands_by_case: dict[str, list[dict]],
    events_by_case: dict[str, list[dict]],
) -> dict:
    agents = list(catalog.routable_ids())
    commands = command_columns()
    departments = {
        name: {
            "agents": {a: 0 for a in agents},
            "commands": {c: {"claimed": 0, "refused": 0} for c in commands},
        }
        for name in catalog.departments
    }
    events = {event_type: 0 for event_type in catalog.event_routes}

    for case in cases:
        case_id = case.get("case_id")
        seen_event_types = {
            row.get("event_type") for row in events_by_case.get(case_id, [])
        }
        for event_type in seen_event_types:
            if event_type in events:
                events[event_type] += 1
        routing = case.get("routing") or {}
        dept = departments.get(routing.get("department"))
        if dept is None:
            continue  # counted in the population, attributed to no row
        for agent in routing.get("route") or []:
            if agent in dept["agents"]:
                dept["agents"][agent] += 1
        for row in commands_by_case.get(case_id, []):
            action = row.get("action")
            if action in dept["commands"]:
                dept["commands"][action]["claimed"] += 1
        for refused in case.get("refused_commands") or []:
            action = refused.get("action") if isinstance(refused, dict) else None
            if action in dept["commands"]:
                dept["commands"][action]["refused"] += 1

    return {"population": len(cases), "departments": departments, "events": events}
