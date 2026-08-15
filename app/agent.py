# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Keplaria workflow — the durable post-onboarding lifecycle graph.

event → reload durable case state → classify → (agentic path only)
structured routing decision → validated branch → grounded evidence
extraction → sanctions screening → compliance interpretation (when there are
candidates to weigh) → deterministic risk gate → command queue.

load_case_state is the first node on every path. It reloads the case's
durable lifecycle and certificate blocks from Firestore and classifies the
inbound event as `agentic` (a human- or document-driven event: a new
supplier packet or a received certificate) or `clock` (a scheduler-driven
event: a renewal due date or an overdue evidence check). A clock event
bypasses the coordinator entirely and goes straight to the risk gate — it
carries no document and needs no delegation decision, so routing it through
an LlmAgent would spend a model call to be told "no agents" and put a
delegation decision in the trace that was never made.

An agentic event reaches the coordinator, which proposes a route; apply_route
validates that proposal against deterministic policy and picks the branch
(evidence extraction, compliance screening, both, or neither). When evidence
is routed, the Evidence agent extracts fields from the document and
validate_evidence — a deterministic grounding check, not a model call —
independently confirms every extracted value resolves to a verbatim span on
the exact document supplied, bounded to one retry before quarantining. No
model output reaches a side effect unvalidated.

Every path, agentic or clock, converges on assess_risk: the deterministic
risk gate that decides whether the case may be onboarded, renewed, or held at
all. A flagged or otherwise non-clear case never reaches the command queue.

The screening node reaches the self-hosted yente service on the private VM. It
has no public address, so that call only succeeds when the serving workload has
private VPC connectivity — on Agent Runtime, a PSC-I network attachment. When
screening returns candidates, the tool-less Compliance agent interprets them
against policy, and apply_compliance — a deterministic check, not a model
call — independently confirms every candidate id it references actually came
from the screen before the assessment is allowed to reach the gate. An
unreachable service or a screen with no candidates has nothing to interpret,
so it skips the agent and feeds the gate directly.

The graph never calls the ERP itself: commit_commands only claims the
commands `app.lifecycle.decide` names (see app/nodes.py's docstring for why —
the same PSC-I attachment has no public internet egress). app.executor.runner,
driven by the ingress, does the actual write.
"""

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.workflow import Workflow
from google.genai import types

from app.nodes import (
    apply_compliance,
    apply_route,
    assess_risk,
    commit_commands,
    load_case_state,
    park_case,
    parse_case,
    quarantine_case,
    screen_supplier,
    validate_evidence,
)
from app.schemas import ComplianceAssessment, EvidenceResult, RoutingDecision

coordinator = LlmAgent(
    name="mission_coordinator",
    model="gemini-3.6-flash",
    # {event_type}, {supplier_name} and {has_document} are ADK state-template
    # placeholders, the same mechanism evidence_agent and compliance_agent use
    # below. They are not decoration: this node's input is load_case_state's
    # output, `{"event_class": "agentic"}`, which carries the event's CLASS and
    # never its TYPE. Without these placeholders the only place the event type
    # appears is parse_case's output sitting in session history, and reading
    # the class instead of the type is exactly what produced an empty route on
    # a new_supplier_packet — an event whose every valid answer names at least
    # one agent. app.nodes.load_case_state publishes all three as flat
    # top-level state keys, which is the only shape inject_session_state can
    # resolve.
    instruction=(
        "You are the Mission Coordinator for a supplier onboarding workflow.\n"
        "Decide which specialist agents must be engaged for the event below.\n\n"
        "Event type: {event_type}\n"
        "Supplier: {supplier_name}\n"
        "Document attached: {has_document}\n\n"
        "Available agents:\n"
        "  - evidence: extracts grounded corporate fields from submitted documents.\n"
        "  - compliance: screens the legal entity against a sanctions service.\n\n"
        "Rules, keyed on the event type exactly as written above:\n"
        "  - new_supplier_packet: a brand new supplier. Both evidence and "
        "compliance are required, because nothing about the entity is verified yet.\n"
        "  - certificate_received: a document arrived for a known supplier. "
        "Engage evidence only; do not re-screen unless entity fields changed.\n\n"
        "Both event types above require at least one agent. Never return an "
        "empty route for either of them, and never answer that the event does "
        "not match a known workflow: if the event type is one of the two "
        "named above, apply its rule. Decide from the event type alone — the "
        "presence or absence of a document does not change which agents are "
        "required.\n\n"
        "Return the agents in the route field and one sentence of justification "
        "in the reason field. Never invent agent names."
    ),
    output_schema=RoutingDecision,
    output_key="routing_decision",
    # Routing must be reproducible run to run; this is a control-flow decision,
    # not a creative one.
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

evidence_agent = LlmAgent(
    name="evidence_agent",
    model="gemini-3.6-flash",
    # {document_checksum} and {document_pages} are ADK state-template
    # placeholders, not Python format fields — a plain str instruction is
    # run through instructions_utils.inject_session_state, which resolves a
    # bare {identifier} against a top-level session-state key. app.nodes
    # .load_case_state is what populates these two keys from the derivative
    # it loads. Without a placeholder here the model would never see the
    # document at all: the edge into this agent carries apply_route's
    # routing decision as its content, not the document.
    instruction=(
        "You extract corporate fields from a supplier document.\n\n"
        "Document checksum: {document_checksum}\n\n"
        "Document pages, each labeled with its zero-based index:\n"
        "{document_pages}\n\n"
        "Extract every field you can support, and for each one return the "
        "verbatim span of page text the value came from.\n\n"
        "Rules:\n"
        "  - Copy document_checksum exactly as given above. Never alter it.\n"
        "  - Every value MUST appear inside the span you cite, and the span "
        "MUST appear verbatim on the page you cite. An independent validator "
        "checks both, and an unsupported value quarantines the case.\n"
        "  - Extract 'certificate_expiry' as an ISO date (YYYY-MM-DD).\n"
        "  - If a field is not in the document, omit it. Never guess."
    ),
    output_schema=EvidenceResult,
    output_key="evidence_result",
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

compliance_agent = LlmAgent(
    name="compliance_agent",
    model="gemini-3.6-flash",
    # {screening_supplier_name} and {screening_candidates} are the same kind
    # of state-template placeholder as evidence_agent's above: screen_supplier
    # publishes both as flat, top-level session-state keys precisely so a
    # plain-string instruction here can resolve them. This agent carries no
    # tools, so those two keys are the only way the screening result reaches
    # it at all.
    instruction=(
        "You interpret sanctions-screening candidates for a supplier under a "
        "compliance policy.\n\n"
        "Supplier being screened: {screening_supplier_name}\n\n"
        "Candidates returned by the screening service:\n"
        "{screening_candidates}\n\n"
        "For every candidate, return its candidate_id copied exactly as given, "
        "whether it plausibly refers to the same entity as the supplier "
        "(consider name similarity and topics), and one sentence of reasoning.\n\n"
        "Recommendation rules:\n"
        "  - Any candidate with match: true -> 'corroborate_block'.\n"
        "  - No confirmed match, but at least one candidate that plausibly "
        "concerns this supplier -> 'escalate_review'.\n"
        "  - Every candidate clearly unrelated -> 'note_clear'.\n\n"
        "Never invent candidate ids. Never state that the supplier is cleared "
        "or approved: the final decision belongs to a deterministic policy "
        "gate; your output is an interpretation for the audit record."
    ),
    output_schema=ComplianceAssessment,
    output_key="compliance_assessment",
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)

root_agent = Workflow(
    name="keplaria_workflow",
    edges=[
        ("START", parse_case),
        (parse_case, load_case_state),
        # The coordinator bypass: clock-driven events engage no agents, so
        # they never reach an LlmAgent. They still pass through assess_risk,
        # which keeps the "every write path carries a verdict" invariant.
        (load_case_state, {"agentic": coordinator, "clock": assess_risk}),
        (coordinator, apply_route),
        # A routing-map chain element is this ADK version's syntax for a
        # conditional edge. "skip" goes to assess_risk rather than straight to
        # commit_commands so that EVERY path to the command queue carries a
        # policy verdict — that invariant is what lets the executor refuse a
        # case with no verdict instead of having to tolerate one.
        (
            apply_route,
            {
                "evidence": evidence_agent,
                "screen": screen_supplier,
                "skip": assess_risk,
                "blocked": quarantine_case,
            },
        ),
        (evidence_agent, validate_evidence),
        # The back-edge is the bounded retry: one fresh extraction, then
        # quarantine. Verified against ADK 2.5.0 — Workflow performs no cycle
        # validation and ctx.state persists across the loop, so the attempt
        # counter in validate_evidence is what bounds it.
        (
            validate_evidence,
            {
                "retry": evidence_agent,
                "screen": screen_supplier,
                "skip": assess_risk,
                "ungrounded": quarantine_case,
            },
        ),
        (
            screen_supplier,
            {"interpret": compliance_agent, "score": assess_risk},
        ),
        (compliance_agent, apply_compliance),
        (apply_compliance, assess_risk),
        # The gate. Only "clear" reaches the write terminal.
        (
            assess_risk,
            {
                "clear": commit_commands,
                "review": park_case,
                "blocked": quarantine_case,
            },
        ),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)
