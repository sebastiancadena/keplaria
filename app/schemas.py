"""Typed contracts shared by the ingress adapter and the graph.

Both sides of the Pub/Sub boundary validate against the same models, so a
malformed event is rejected at the edge rather than halfway through a workflow.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class CanonicalEvent(BaseModel):
    """The versioned event every producer must emit."""

    event_id: str
    case_id: str
    event_type: str
    supplier: str
    schema_version: int = 1
    # The originating department. Required from schema_version 2; a v1
    # event without one is grandfathered onto the catalog's
    # legacy.v1_department at routing time (app/nodes.py::apply_route) —
    # resolution deliberately does NOT happen here, because the schema
    # module must not read the catalog and the routing record captures
    # which source the department came from. Clock-event producers stamp
    # "procurement" by convention: the scheduler acts on behalf of the
    # supplier lifecycle procurement owns. That is a modeling convention,
    # not a claim of human origin. The department is a policy-and-audit
    # label asserted by the producer, not an authenticated identity.
    department: str | None = None
    amount: float | None = None
    # When set, the event is only valid against this exact stored case version.
    expected_case_version: int | None = None
    # The demo clock. Station-keeping decisions are a pure function of this
    # date, never of wall-clock time, so a simulated year runs in seconds.
    effective_date: str | None = None
    # Immutable payload reference; resolved by app.documents.load_document.
    document_ref: str | None = None

    @model_validator(mode="after")
    def _require_department_at_v2(self) -> "CanonicalEvent":
        if self.schema_version >= 2 and not self.department:
            raise ValueError(
                "schema_version >= 2 requires a non-empty department"
            )
        return self


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


class CandidateAssessment(BaseModel):
    """The Compliance agent's reading of one screening candidate."""

    candidate_id: str = Field(
        description="The candidate id from the screening result, copied exactly."
    )
    relevant: bool = Field(
        description="Whether this candidate plausibly refers to the supplier."
    )
    reasoning: str = Field(description="One or two sentences of justification.")


class ComplianceAssessment(BaseModel):
    """The Compliance agent's structured output. Checked by app.nodes.apply_compliance.

    `recommendation` stays a plain string: the allowed vocabulary is enforced
    deterministically by the validator so an out-of-vocabulary value becomes a
    recorded invalid assessment (fail closed), not a hard schema error.
    """

    assessments: list[CandidateAssessment] = Field(
        description="One entry per screening candidate considered."
    )
    recommendation: str = Field(
        description=(
            "One of 'corroborate_block', 'escalate_review', 'note_clear'."
        )
    )
    rationale: str = Field(description="Overall human-readable justification.")
