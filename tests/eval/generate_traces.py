"""Run the domain eval dataset through the real graph and write grading traces.

`agents-cli eval generate` cannot collect this graph's stream: its SSE
parser requires every event to carry `author` and `content`, and Workflow
function nodes legitimately emit state-only events with neither (verified
against agents-cli 1.3.1, google/agents/cli/eval/cmd_generate.py). This
module replaces only the *generate* stage — grading stays in
`agents-cli eval grade`, which consumes the canonical EvaluationDataset
trace shape this file writes (see
.agents/skills/google-agents-cli-eval/references/dataset_schema.md).

Each produced eval case carries:

- ``prompt`` — the canonical event JSON, unchanged from the dataset;
- ``agent_data.turns[0].events`` — one event per graph node output, author
  set to the emitting node, content carrying the output as JSON text;
- ``responses[0]`` — the authoritative post-run Firestore case document
  (phase, routing, policy, lifecycle, certificate, outbox commands), which
  is what the deterministic ``domain_case_pass`` metric grades against.

Cases run sequentially: determinism matters more than wall-clock here, and
the coordinator runs at temperature 0.

Run (emulator + yente stub already up, seeded):

    FIRESTORE_EMULATOR_HOST=localhost:8451 ... \
    uv run --env-file .env python tests/eval/generate_traces.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
    sys.exit("refusing to run against real Firestore: set FIRESTORE_EMULATOR_HOST")

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from app.agent import app  # noqa: E402
from app.state.firestore import CASES, OUTBOX, get_client  # noqa: E402

DATASET = Path(__file__).parent / "datasets" / "domain-dataset.json"
OUT = Path("artifacts/traces/domain_traces.json")

AGENTS_MAP = {
    "mission_coordinator": {
        "agent_id": "mission_coordinator",
        "agent_type": "LlmAgent",
        "instruction": "Structured routing over canonical supplier events.",
    },
    "evidence_agent": {
        "agent_id": "evidence_agent",
        "agent_type": "LlmAgent",
        "instruction": "Grounded field extraction from the redacted derivative.",
    },
}


async def run_case(prompt_text: str) -> list[dict]:
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="domain-eval"
    )
    events: list[dict] = []
    async for ev in runner.run_async(
        user_id="domain-eval",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text=prompt_text)]
        ),
    ):
        if ev.output is None:
            continue
        output = ev.output
        if hasattr(output, "model_dump"):
            output = output.model_dump()
        events.append(
            {
                "author": getattr(ev, "author", None) or "keplaria_workflow",
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(output, default=str)}],
                },
            }
        )
    return events


def case_outcome(case_id: str) -> dict:
    db = get_client()
    snap = db.collection(CASES).document(case_id).get()
    doc = snap.to_dict() or {}
    commands = [
        {"id": cmd.id, **{k: cmd.to_dict().get(k) for k in ("status", "cycle")}}
        for cmd in db.collection(CASES).document(case_id).collection(OUTBOX).stream()
    ]
    return {
        "case_id": case_id,
        "phase": doc.get("phase"),
        "routing": doc.get("routing"),
        "policy": doc.get("policy"),
        "lifecycle": doc.get("lifecycle"),
        "certificate": doc.get("certificate"),
        "commands": commands,
    }


async def main() -> None:
    dataset = json.loads(DATASET.read_text())
    traces = []
    for case in dataset["eval_cases"]:
        prompt = case["prompt"]
        prompt_text = prompt["parts"][0]["text"]
        case_id = json.loads(prompt_text)["case_id"]
        print(f"running {case['eval_case_id']} ({case_id}) ...", flush=True)
        node_events = await run_case(prompt_text)
        outcome = case_outcome(case_id)
        traces.append(
            {
                "eval_case_id": case["eval_case_id"],
                "prompt": prompt,
                "responses": [
                    {
                        "response": {
                            "role": "model",
                            "parts": [{"text": json.dumps(outcome, default=str)}],
                        }
                    }
                ],
                "agent_data": {
                    "agents": AGENTS_MAP,
                    "turns": [
                        {
                            "turn_index": 0,
                            "turn_id": "turn_0",
                            "events": [
                                {"author": "user", "content": prompt},
                                *node_events,
                            ],
                        }
                    ],
                },
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"eval_cases": traces}, indent=2, default=str))
    print(f"wrote {len(traces)} traces to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
