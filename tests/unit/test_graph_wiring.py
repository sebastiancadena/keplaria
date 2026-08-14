"""Graph wiring is load-bearing: every path to the command queue passes the gate.

This module pins the invariant that prevents a critical bypass: a flagged supplier
must never reach the ERP command queue. The workflow's `assess_risk` node implements
the policy gate and must sit in the critical path from BOTH the screening branch
(screen_supplier) and the non-screening branch (skip). If a later change shortcuts
one of these paths directly to queue_supplier, this test fails loudly rather than
silently shipping an onboarding of a flagged entity.

All assertions read `root_agent.edges` directly — no model calls, no network, no
external state. This is pure structural introspection and belongs in the default
suite, not in live-marked tests.
"""

import pytest

from app.agent import evidence_agent, root_agent
from app.nodes import (
    MAX_EVIDENCE_ATTEMPTS,
    assess_risk,
    park_case,
    queue_supplier,
    quarantine_case,
    screen_supplier,
    validate_evidence,
)


def test_flagged_supplier_never_reaches_the_command_queue():
    """Every path to queue_supplier passes through assess_risk.

    The risk gate (assess_risk) must be the sole point where a policy verdict is
    applied before the command queue. Both the screening branch and the skip branch
    feed into it, ensuring no path bypasses the verdict.
    """
    edges = {}
    for edge in root_agent.edges:
        source = edge[0]
        target = edge[1]
        name = getattr(source, "__name__", str(source))
        edges[name] = target

    assert edges["screen_supplier"] is assess_risk, "screening must feed the gate"
    assert edges["assess_risk"] == {
        "clear": queue_supplier,
        "review": park_case,
        "blocked": quarantine_case,
    }
    assert edges["apply_route"] == {
        "evidence": evidence_agent,
        "screen": screen_supplier,
        "skip": assess_risk,
        "blocked": quarantine_case,
    }


class _StubContext:
    """validate_evidence only reads ctx.state — a dict wrapper is enough.

    Same shape as the stub in tests/unit/test_nodes_risk.py; this file has
    none of its own yet.
    """

    def __init__(self, state: dict):
        self.state = state


def test_the_evidence_agent_holds_no_operational_tools():
    assert not getattr(evidence_agent, "tools", []), (
        "Evidence may call neither the screening service nor the ERP"
    )
    assert evidence_agent.disallow_transfer_to_parent is True
    assert evidence_agent.disallow_transfer_to_peers is True


def test_the_evidence_agent_instruction_references_the_document_state_keys():
    """The only thing that gets the document into the model's prompt is a
    {key} placeholder in a plain-string instruction — ADK's state-injection
    resolves those against top-level session-state keys
    (google.adk.utils.instructions_utils.inject_session_state). If a future
    edit drops the placeholder, the agent silently goes back to being asked
    to cite a document it was never shown; this pins the placeholder's
    presence so that edit fails loudly instead."""
    assert isinstance(evidence_agent.instruction, str), (
        "state injection only runs for a plain string instruction "
        "(LlmAgent.canonical_instruction bypasses it for a callable one)"
    )
    assert "{document_checksum}" in evidence_agent.instruction
    assert "{document_pages}" in evidence_agent.instruction


@pytest.mark.asyncio
async def test_the_evidence_agent_instruction_renders_the_actual_document():
    """Exercises ADK's real state-injection mechanism end to end, rather than
    trusting that the placeholders in the instruction are wired to anything.
    load_case_state populates document_checksum/document_pages in session
    state; this proves inject_session_state — the same function ADK's LLM
    flow calls before every model turn — actually substitutes them into the
    rendered prompt text."""
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.agents.readonly_context import ReadonlyContext
    from google.adk.sessions import InMemorySessionService
    from google.adk.utils.instructions_utils import inject_session_state

    service = InMemorySessionService()
    session = await service.create_session(
        app_name="test",
        user_id="u1",
        state={
            "document_checksum": "abc123",
            "document_pages": "Page 0:\nExpiry: 2027-01-01",
        },
    )
    ctx = InvocationContext(session_service=service, invocation_id="inv-1", session=session)

    rendered = await inject_session_state(evidence_agent.instruction, ReadonlyContext(ctx))

    assert "abc123" in rendered
    assert "Expiry: 2027-01-01" in rendered


def _routing_maps(workflow):
    """Every conditional edge in the graph, as {source: {route: target}}."""
    return {edge[0]: edge[1] for edge in workflow.edges if isinstance(edge[1], dict)}


def test_a_clock_event_never_reaches_an_llm_agent():
    from google.adk.agents import LlmAgent
    from app.nodes import load_case_state

    clock_target = _routing_maps(root_agent)[load_case_state]["clock"]

    assert not isinstance(clock_target, LlmAgent), (
        "a clock event engages no agents; routing one to an LlmAgent spends a "
        "model call to be told 'no agents' and puts a delegation decision in "
        "the trace that was never made"
    )


def test_ungrounded_evidence_retries_once_then_quarantines():
    derivative = {"checksum": "abc123", "pages": ["Expiry: 2027-01-01"]}
    hallucinated = {"document_checksum": "abc123",
                    "fields": [{"name": "certificate_expiry", "value": "2030-01-01",
                                "page": 0, "span": "Expiry: 2027-01-01",
                                "confidence": 0.9}]}

    ctx = _StubContext({"case": {"case_id": "C1", "event_type": "certificate_received"},
                "derivative": derivative, "routing": {"route": ["evidence"]}})

    first = validate_evidence(hallucinated, ctx)
    assert first.actions.route == "retry"
    ctx.state.update(first.actions.state_delta)

    second = validate_evidence(hallucinated, ctx)
    assert second.actions.route == "ungrounded", (
        f"the retry is bounded at {MAX_EVIDENCE_ATTEMPTS} attempts"
    )


def test_grounded_evidence_continues_to_screening_when_compliance_is_routed():
    derivative = {"checksum": "abc123", "pages": ["Expiry: 2027-01-01"]}
    good = {"document_checksum": "abc123",
            "fields": [{"name": "certificate_expiry", "value": "2027-01-01",
                        "page": 0, "span": "Expiry: 2027-01-01", "confidence": 0.9}]}

    ctx = _StubContext({"case": {"case_id": "C1", "event_type": "new_supplier_packet"},
                "derivative": derivative,
                "routing": {"route": ["evidence", "compliance"]}})

    event = validate_evidence(good, ctx)

    assert event.actions.route == "screen"
    assert event.actions.state_delta["evidence"]["fields"][0]["value"] == "2027-01-01"


def test_grounded_evidence_skips_screening_when_compliance_is_not_routed():
    derivative = {"checksum": "abc123", "pages": ["Expiry: 2027-01-01"]}
    good = {"document_checksum": "abc123",
            "fields": [{"name": "certificate_expiry", "value": "2027-01-01",
                        "page": 0, "span": "Expiry: 2027-01-01", "confidence": 0.9}]}

    ctx = _StubContext({"case": {"case_id": "C1", "event_type": "certificate_received"},
                "derivative": derivative, "routing": {"route": ["evidence"]}})

    assert validate_evidence(good, ctx).actions.route == "skip"


def test_absent_evidence_quarantines_immediately():
    ctx = _StubContext({"case": {"case_id": "C1", "event_type": "certificate_received"},
                "derivative": None, "routing": {"route": ["evidence"]}})

    assert validate_evidence({}, ctx).actions.route == "ungrounded"
