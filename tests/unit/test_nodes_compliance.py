"""Unit tests for the compliance assessment path: schema, validator, escalation."""

from __future__ import annotations

from app.schemas import CandidateAssessment, ComplianceAssessment


def test_compliance_assessment_parses_a_complete_payload():
    parsed = ComplianceAssessment(
        assessments=[
            {"candidate_id": "c-1", "relevant": True, "reasoning": "same name"}
        ],
        recommendation="escalate_review",
        rationale="one plausible candidate",
    )
    assert parsed.assessments[0].candidate_id == "c-1"
    assert parsed.recommendation == "escalate_review"
