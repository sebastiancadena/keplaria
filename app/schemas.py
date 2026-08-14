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
    # The demo clock. Station-keeping decisions are a pure function of this
    # date, never of wall-clock time, so a simulated year runs in seconds.
    effective_date: str | None = None
    # Immutable payload reference; resolved by app.documents.load_document.
    document_ref: str | None = None


class EvidenceField(BaseModel):
    """One extracted value and the span that supports it."""

    name: str = Field(description="Field name, e.g. 'certificate_expiry'.")
    value: str = Field(description="The extracted value.")
    page: int = Field(description="Zero-based page index the value came from.")
    span: str = Field(
        description="The verbatim text from that page containing the value."
    )
    confidence: float = Field(description="Extraction confidence, 0.0 to 1.0.")


class EvidenceResult(BaseModel):
    """The Evidence agent's structured output. Checked by app.grounding."""

    document_checksum: str = Field(
        description="The checksum of the document supplied, copied exactly."
    )
    fields: list[EvidenceField] = Field(description="Every field extracted.")


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
