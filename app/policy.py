"""Deterministic routing policy, derived from the fleet catalog.

The coordinator proposes a route; this module decides whether it is
allowed. Compliance-critical control flow is code plus versioned data,
never model output. catalog/fleet.v1.json is the single source of the
event-type allowlist, the routable agent set, and department scope; a
catalog that cannot load refuses every proposal (CATALOG_UNAVAILABLE).
There is deliberately NO fallback map — a fallback would silently revert
authority to stale data and would make the fail-closed test meaningless.

The department dimension is a policy-and-audit boundary, not a security
boundary: the department on an event is asserted by its producer, and what
this module's consumers guarantee is that an out-of-scope proposal is
refused and durably recorded — never that a mislabeled producer is
prevented from anything.
"""

from __future__ import annotations

from app.catalog import CLOCK_EVENTS, CatalogLoadError, get_catalog

__all__ = [
    "CLOCK_EVENTS",
    "PolicyError",
    "allowed_routes",
    "resolve_department",
    "validate_route",
]


class PolicyError(ValueError):
    """Raised when a proposed route violates policy."""


def _catalog():
    try:
        return get_catalog()
    except CatalogLoadError as exc:
        raise PolicyError(f"CATALOG_UNAVAILABLE: {exc}") from exc


def allowed_routes() -> dict[str, set[str]]:
    """The event-type allowlist, derived from the catalog.

    An accessor rather than a module constant: a constant would load the
    catalog at import time, and a malformed artifact failing at import
    presents on the serving platform as a log-less "failed to start".
    """
    return {
        event_type: set(route)
        for event_type, route in _catalog().event_routes.items()
    }


def resolve_department(department: str | None) -> tuple[str, str]:
    """Return (effective_department, source).

    `source` is "event" when the event carried a department, or
    "legacy_default" when a v1 event was grandfathered onto the catalog's
    legacy department. Raises PolicyError(UNKNOWN_DEPARTMENT) when no
    department was given and the grandfather clause is null (the sunset
    state — ending the grace period is a catalog edit, not a code change),
    and PolicyError(CATALOG_UNAVAILABLE) when the catalog cannot load.

    Deliberately does NOT verify a producer-supplied department is
    declared: that is validate_route's own check, so the refusal lands in
    the routing record with the department that claimed it.
    """
    if department:
        return department, "event"
    legacy = _catalog().legacy.v1_department
    if legacy is None:
        raise PolicyError(
            "UNKNOWN_DEPARTMENT: event carries no department and the "
            "v1 grandfather clause is null"
        )
    return legacy, "legacy_default"


def validate_route(event_type: str, route: list[str], department: str) -> list[str]:
    """Return the permitted subset of `route`, in canonical order, or raise.

    Two authorities intersect here, with distinct outcomes on violation:

    - The DEPARTMENT scope refuses. A reach for an agent outside the
      calling department's permitted_agents is an authorization violation
      and must be visible — quarantined, recorded, on the console — so it
      raises DEPARTMENT_FORBIDS_AGENT, and it does so BEFORE event-type
      narrowing: an agent that is both department-forbidden and
      event-type-disallowed refuses rather than silently dropping.
    - The EVENT TYPE narrows. A known, department-permitted agent the
      event type doesn't allow is dropped from the returned route, exactly
      as before the department dimension existed: the coordinator
      over-proposing inside its own scope is a model mistake, not a
      violation worth denying the whole event over.

    The department is a policy-and-audit label asserted by the producer,
    not an authenticated identity: what this function guarantees is that
    an out-of-scope proposal is refused and recorded, nothing more.

    Still raising, unchanged from before: an unknown event type, an agent
    name this system has never heard of, and a genuinely empty proposal on
    an event type that requires at least one agent (a check on what was
    *proposed* — a proposal that named only event-type-disallowed agents
    narrows to [] without raising).
    """
    catalog = _catalog()
    known = catalog.routable_ids()

    if event_type not in catalog.event_routes:
        raise PolicyError(f"UNKNOWN_EVENT_TYPE: {event_type!r}")

    allowed = set(catalog.event_routes[event_type])
    requested = list(dict.fromkeys(route))

    for agent in requested:
        if agent not in known:
            raise PolicyError(f"UNKNOWN_AGENT: {agent!r}")

    scope = catalog.departments.get(department)
    if scope is None:
        raise PolicyError(f"UNKNOWN_DEPARTMENT: {department!r}")

    for agent in requested:
        if agent not in scope.permitted_agents:
            raise PolicyError(
                f"DEPARTMENT_FORBIDS_AGENT: {agent!r} is outside the "
                f"{department!r} scope"
            )

    if allowed and not requested:
        raise PolicyError(
            f"EMPTY_ROUTE: event type {event_type!r} requires at least one agent"
        )

    permitted = [agent for agent in requested if agent in allowed]
    return [agent for agent in known if agent in permitted]
