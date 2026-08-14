"""Day-2 controlled-503 retry spike.

The document dependency returns one failure (a controlled 503) while the
runner stays alive. `RetryConfig(max_attempts=3, exceptions=[...])` must
retry the failing node; the ledger must show exactly two fetch attempts and
exactly one downstream write — retries never duplicate side effects.

Run: uv run python spikes/retry_503/spike.py  (exits 0 on PASS)
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.adk import Event, Workflow
from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.workflow import FunctionNode, RetryConfig
from google.genai import types


class DocumentDependencyError(Exception):
    """Controlled failure standing in for a 503 from the document store."""


ATTEMPTS = {"fetch": 0}
LEDGER = {"writes": 0}


def fetch_document(node_input: str) -> Event:
    ATTEMPTS["fetch"] += 1
    if ATTEMPTS["fetch"] == 1:
        raise DocumentDependencyError("503 from document store (controlled)")
    return Event(output={"doc": "redacted-derivative", "attempts": ATTEMPTS["fetch"]})


def write_downstream(node_input: dict) -> Event:
    LEDGER["writes"] += 1
    return Event(output={"status": "written", **node_input})


workflow = Workflow(
    name="retry_spike",
    edges=[
        (
            "START",
            FunctionNode(
                func=fetch_document,
                retry_config=RetryConfig(
                    max_attempts=3,
                    initial_delay=0.1,
                    exceptions=[DocumentDependencyError],
                ),
            ),
            write_downstream,
        )
    ],
)

app = App(root_agent=workflow, name="retry_spike_app")


async def main() -> int:
    runner = Runner(
        app=app,
        session_service=InMemorySessionService(),
        auto_create_session=True,
    )
    final_output = None
    async for event in runner.run_async(
        user_id="spike",
        session_id="retry-spike-1",
        new_message=types.Content(role="user", parts=[types.Part.from_text(text="go")]),
    ):
        output = getattr(event, "output", None)
        if isinstance(output, dict) and output.get("status") == "written":
            final_output = output

    criteria = {
        "completed_after_retry": final_output is not None,
        "exactly_two_attempts": ATTEMPTS["fetch"] == 2,
        "exactly_one_downstream_write": LEDGER["writes"] == 1,
    }
    print(f"[spike] final output: {final_output}")
    print(f"[spike] attempts={ATTEMPTS['fetch']} writes={LEDGER['writes']}")
    verdict = "PASS" if all(criteria.values()) else "FAIL"
    print(f"[spike] VERDICT: {verdict} {criteria}")

    # Gate evidence goes in the repo, never stdout-only (see CLAUDE.md
    # Maintenance).
    evidence = {
        "spike": "retry_503",
        "gate": "day-2 controlled-503 retry spike",
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "criteria": criteria,
        "fetch_attempts": ATTEMPTS["fetch"],
        "downstream_writes": LEDGER["writes"],
        "final_output": final_output,
        "verdict": verdict,
    }
    evidence_path = Path(__file__).parent / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"[spike] evidence written to {evidence_path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
