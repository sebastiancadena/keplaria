"""Deterministic routing policy, derived from the fleet catalog.

The coordinator proposes a route; this module decides whether it is
allowed. Compliance-critical control flow is code plus versioned data,
never model output. catalog/fleet.v1.json is the single source of the
event-type allowlist and the routable agent set; a catalog that cannot
load refuses every proposal (CATALOG_UNAVAILABLE). There is deliberately
NO fallback map — a fallback would silently revert authority to stale
data and would make the fail-closed test meaningless.
"""

from __future__ import annotations

from app.catalog import CLOCK_EVENTS, CatalogLoadError, get_catalog

__all__ = ["CLOCK_EVENTS", "PolicyError", "allowed_routes", "validate_route"]


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


def validate_route(event_type: str, route: list[str]) -> list[str]:
    """Return the permitted subset of `route`, in canonical order, or raise.

    Canonical order is the catalog's declaration order (evidence before
    compliance — the loader enforces it, because screening consumes
    grounded entity fields the evidence step produces).

    A known agent the event type doesn't permit is *dropped*, not refused:
    the coordinator over-proposing (e.g. naming `compliance` on a
    `certificate_received` event) is a model mistake about scope, not a
    policy violation worth denying the whole event over. Two things still
    raise, because narrowing can't repair either: an agent name this
    system has never heard of, and a genuinely empty proposal on an event
    type that requires at least one agent. That second check is about what
    the coordinator *proposed*, not about what survives narrowing.
    """
    catalog = _catalog()
    known = catalog.routable_ids()

    if event_type not in catalog.event_routes:
        raise PolicyError(f"unknown event type: {event_type!r}")

    allowed = set(catalog.event_routes[event_type])
    requested = list(dict.fromkeys(route))

    for agent in requested:
        if agent not in known:
            raise PolicyError(f"unknown agent: {agent!r}")

    if allowed and not requested:
        raise PolicyError(f"empty route is invalid for event type {event_type!r}")

    permitted = [agent for agent in requested if agent in allowed]
    return [agent for agent in known if agent in permitted]
