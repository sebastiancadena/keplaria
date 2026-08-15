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
    "renewal_due": set(),
}

# Events driven by the clock rather than by a document or an entity change.
# They engage no agents, so they must not reach the coordinator at all —
# spending a model call to be told "no agents" is waste, and it would make
# the trace imply a delegation decision that never happened.
CLOCK_EVENTS = frozenset({"renewal_due", "evidence_overdue"})


class PolicyError(ValueError):
    """Raised when a proposed route violates policy."""


def validate_route(event_type: str, route: list[str]) -> list[str]:
    """Return the permitted subset of `route`, in canonical order, or raise.

    Canonical order is evidence before compliance, because screening consumes
    grounded entity fields the evidence step produces.

    A known agent the event type doesn't permit is *dropped*, not refused:
    the coordinator over-proposing (e.g. naming `compliance` on a
    `certificate_received` event, where entity screening is not warranted)
    is a model mistake about scope, not a policy violation worth denying the
    whole event over. The guarantee this module exists to hold is narrower
    and still absolute — no agent runs unless this function permits it — and
    dropping the disallowed agent while running the rest satisfies that just
    as well as refusing outright, without quarantining a legitimate business
    event over an LLM's over-caution.

    Only that one check changes from raise to filter. Two things still raise,
    because narrowing can't repair either: an agent name this system has
    never heard of (not a scope mistake — the proposal itself is malformed),
    and a genuinely empty proposal (`route == []`) on an event type that
    requires at least one agent to do anything. That second check is about
    what the coordinator *proposed*, not about what survives narrowing — a
    proposal that named only disallowed agents narrows to an empty route the
    same as any other over-proposal, it does not raise.
    """
    if event_type not in ALLOWED_ROUTES:
        raise PolicyError(f"unknown event type: {event_type!r}")

    allowed = ALLOWED_ROUTES[event_type]
    requested = list(dict.fromkeys(route))

    for agent in requested:
        if agent not in KNOWN_AGENTS:
            raise PolicyError(f"unknown agent: {agent!r}")

    if allowed and not requested:
        raise PolicyError(f"empty route is invalid for event type {event_type!r}")

    permitted = [agent for agent in requested if agent in allowed]
    return [agent for agent in KNOWN_AGENTS if agent in permitted]
