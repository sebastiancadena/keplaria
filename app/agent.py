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

"""Keplaria workflow — durable-HITL spine.

Minimal deterministic graph: case intake → RequestInput pause → decision
processing, on a resumable App whose events persist in Agent Platform
Sessions. A paused approval must survive the death of the serving process
and resume in a fresh one. The full multi-agent graph grows from this spine;
no LLM node is involved yet, so runs are deterministic and token-free.
"""

import json

from google.adk import Context, Event, Workflow
from google.adk.apps import App, ResumabilityConfig
from google.adk.events import RequestInput


def parse_case(node_input: str, ctx: Context) -> Event:
    """Parse the inbound case payload and stash it for the approval node."""
    try:
        case = json.loads(node_input)
    except json.JSONDecodeError:
        case = {"raw": str(node_input)[:200]}
    ctx.state["case_data"] = case
    return Event(output=case)


def request_approval(node_input, ctx: Context):  # type: ignore[no-untyped-def]
    """Pause the workflow until a human approves or rejects the case."""
    yield RequestInput(
        message="Case requires human approval. Approve or reject.",
        payload=ctx.state.get("case_data", {}),
    )


def process_decision(node_input, ctx: Context) -> Event:  # type: ignore[no-untyped-def]
    """Turn the human's RequestInput reply into a terminal case status."""
    decision = "unknown"
    raw = node_input
    if isinstance(raw, dict):
        # either {"decision": ...} directly or {"result": "<json string>"}
        if "decision" in raw:
            decision = raw["decision"]
        elif "result" in raw:
            raw = raw["result"]
    if isinstance(raw, str) and decision == "unknown":
        try:
            decision = json.loads(raw).get("decision", "unknown")
        except (json.JSONDecodeError, AttributeError):
            decision = "approve" if "approve" in raw.lower() else "reject"

    status = "approved" if decision == "approve" else "rejected"
    return Event(output={"status": status, "case": ctx.state.get("case_data", {})})


root_agent = Workflow(
    name="keplaria_workflow",
    edges=[("START", parse_case, request_approval, process_decision)],
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)
