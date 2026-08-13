"""Deterministic routing policy.

The coordinator proposes a route; this module decides whether it is allowed.
Compliance-critical control flow is code, not model output.
"""

from __future__ import annotations

KNOWN_AGENTS = ("evidence", "compliance")

# Which agents each event type may engage. An event type mapping to the empty
# set is handled deterministically and must not spend an LLM call.
ALLOWED_ROUTES: dict[str, set[str]] = {
    "new_supplier_packet": {"evidence", "compliance"},
    "certificate_received": {"evidence"},
    "evidence_overdue": set(),
}


class PolicyError(ValueError):
    """Raised when a proposed route violates policy."""


def validate_route(event_type: str, route: list[str]) -> list[str]:
    """Return the permitted route in canonical order, or raise.

    Canonical order is evidence before compliance, because screening consumes
    grounded entity fields the evidence step produces.
    """
    if event_type not in ALLOWED_ROUTES:
        raise PolicyError(f"unknown event type: {event_type!r}")

    allowed = ALLOWED_ROUTES[event_type]
    requested = list(dict.fromkeys(route))

    for agent in requested:
        if agent not in KNOWN_AGENTS:
            raise PolicyError(f"unknown agent: {agent!r}")
        if agent not in allowed:
            raise PolicyError(
                f"agent {agent!r} is not permitted for event type {event_type!r}"
            )

    if allowed and not requested:
        raise PolicyError(f"empty route is invalid for event type {event_type!r}")

    return [agent for agent in KNOWN_AGENTS if agent in requested]
