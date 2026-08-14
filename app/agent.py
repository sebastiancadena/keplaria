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

"""Keplaria workflow — the thin vertical.

event → canonical parse → structured routing decision → validated branch →
sanctions screening → deterministic risk gate → command queue. The
coordinator proposes a route; deterministic policy code decides whether it is
allowed, and a second deterministic node decides whether the screened
supplier may be onboarded at all. No model output reaches a side effect
unvalidated, and a flagged supplier never reaches the command queue.

The screening node reaches the self-hosted yente service on the private VM. It
has no public address, so that call only succeeds when the serving workload has
private VPC connectivity — on Agent Runtime, a PSC-I network attachment.

The graph never calls the ERP itself: queue_supplier only claims the
create_supplier command (see app/nodes.py's docstring for why — the same
PSC-I attachment has no public internet egress). app.executor.runner, driven
by the ingress, does the actual write.
"""

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.workflow import Workflow
from google.genai import types

from app.nodes import (
    apply_route,
    assess_risk,
    park_case,
    parse_case,
    quarantine_case,
    queue_supplier,
    screen_supplier,
)
from app.schemas import RoutingDecision

coordinator = LlmAgent(
    name="mission_coordinator",
    model="gemini-3.6-flash",
    instruction=(
        "You are the Mission Coordinator for a supplier onboarding workflow.\n"
        "You receive a canonical event describing a supplier case. Decide which "
        "specialist agents must be engaged.\n\n"
        "Available agents:\n"
        "  - evidence: extracts grounded corporate fields from submitted documents.\n"
        "  - compliance: screens the legal entity against a sanctions service.\n\n"
        "Rules:\n"
        "  - new_supplier_packet: a brand new supplier. Both evidence and "
        "compliance are required, because nothing about the entity is verified yet.\n"
        "  - certificate_received: a document arrived for a known supplier. "
        "Engage evidence only; do not re-screen unless entity fields changed.\n\n"
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

root_agent = Workflow(
    name="keplaria_workflow",
    edges=[
        ("START", parse_case),
        (parse_case, coordinator),
        (coordinator, apply_route),
        # A routing-map chain element is this ADK version's syntax for a
        # conditional edge. "skip" goes to assess_risk rather than straight to
        # queue_supplier so that EVERY path to the command queue carries a
        # policy verdict — that invariant is what lets the executor refuse a
        # case with no verdict instead of having to tolerate one.
        (
            apply_route,
            {
                "screen": screen_supplier,
                "skip": assess_risk,
                "blocked": quarantine_case,
            },
        ),
        (screen_supplier, assess_risk),
        # The gate. Only "clear" reaches the command queue.
        (
            assess_risk,
            {
                "clear": queue_supplier,
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
