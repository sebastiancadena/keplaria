"""Typed contracts shared by the ingress adapter and the graph.

Both sides of the Pub/Sub boundary validate against the same models, so a
malformed event is rejected at the edge rather than halfway through a workflow.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CanonicalEvent(BaseModel):
    """The versioned event every producer must emit."""

    event_id: str
    case_id: str
    event_type: str
    supplier: str
    schema_version: int = 1
    amount: float | None = None
    # When set, the event is only valid against this exact stored case version.
    expected_case_version: int | None = None


class RoutingDecision(BaseModel):
    """The coordinator's structured output. Validated by policy before use."""

    route: list[str] = Field(
        description="Agents to engage: any of 'evidence', 'compliance'."
    )
    reason: str = Field(description="One sentence explaining the routing choice.")


class ScreeningResult(BaseModel):
    """Outcome of a sanctions screening call, reachable or not."""

    endpoint: str
    supplier: str
    reachable: bool
    candidates: list[dict] = []
    flagged: list[str] = []
    error: str | None = None
