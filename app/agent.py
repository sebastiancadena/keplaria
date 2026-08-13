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

Minimal deterministic graph: case intake → sanctions screening → RequestInput
pause → decision processing, on a resumable App whose events persist in Agent
Platform Sessions. A paused approval must survive the death of the serving
process and resume in a fresh one. The full multi-agent graph grows from this
spine; no LLM node is involved yet, so runs are deterministic and token-free.

The screening node reaches the self-hosted yente service on the private VM. It
has no public address, so this call only succeeds when the serving workload has
private VPC connectivity — on Agent Runtime, a PSC-I network attachment.
"""

import json
import os

import httpx
from google.adk import Context, Event, Workflow
from google.adk.apps import App, ResumabilityConfig
from google.adk.events import RequestInput

YENTE_BASE_URL = os.environ.get("YENTE_BASE_URL", "http://10.10.0.2:8000")
YENTE_DATASET = os.environ.get("YENTE_DATASET", "keplaria_synthetic")
# yente drops results below `cutoff` entirely; the default 0.5 would hide the
# sub-threshold candidates a reviewer needs to judge a false positive.
YENTE_CUTOFF = 0.0


def parse_case(node_input: str, ctx: Context) -> Event:
    """Parse the inbound case payload and stash it for the approval node."""
    try:
        case = json.loads(node_input)
    except json.JSONDecodeError:
        case = {"raw": str(node_input)[:200]}
    ctx.state["case_data"] = case
    return Event(output=case)


def screen_supplier(node_input, ctx: Context) -> Event:  # type: ignore[no-untyped-def]
    """Screen the supplier against self-hosted yente over private VPC.

    Records the outcome in state either way: an unreachable service is a
    result the trace must show, not an exception that hides the network.
    """
    case = ctx.state.get("case_data", {})
    name = case.get("supplier", "")
    query = {
        "queries": {
            "q": {
                "schema": "Company",
                "properties": {"name": [name]},
            }
        }
    }
    screening: dict = {"endpoint": YENTE_BASE_URL, "supplier": name}
    try:
        response = httpx.post(
            f"{YENTE_BASE_URL}/match/{YENTE_DATASET}",
            params={"cutoff": YENTE_CUTOFF},
            json=query,
            timeout=30,
        )
        response.raise_for_status()
        results = response.json()["responses"]["q"]["results"]
        screening["reachable"] = True
        screening["candidates"] = [
            {
                "id": r["id"],
                "caption": r["caption"],
                "score": r["score"],
                "match": r["match"],
                "topics": r.get("properties", {}).get("topics", []),
            }
            for r in results
        ]
        screening["flagged"] = [c["id"] for c in screening["candidates"] if c["match"]]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        screening["reachable"] = False
        screening["error"] = f"{type(exc).__name__}: {exc}"

    ctx.state["screening"] = screening
    return Event(output=screening)


def request_approval(node_input, ctx: Context):  # type: ignore[no-untyped-def]
    """Pause the workflow until a human approves or rejects the case."""
    yield RequestInput(
        message="Case requires human approval. Approve or reject.",
        payload={
            "case": ctx.state.get("case_data", {}),
            "screening": ctx.state.get("screening", {}),
        },
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
    return Event(
        output={
            "status": status,
            "case": ctx.state.get("case_data", {}),
            "screening": ctx.state.get("screening", {}),
        }
    )


root_agent = Workflow(
    name="keplaria_workflow",
    edges=[("START", parse_case, screen_supplier, request_approval, process_decision)],
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)
