"""Exercises the deployed graph shape locally, including a real Gemini call.

Run with:
    FIRESTORE_EMULATOR_HOST=localhost:8451 GOOGLE_CLOUD_PROJECT=keplaria \
    GOOGLE_CLOUD_LOCATION=global YENTE_BASE_URL=http://127.0.0.1:9 \
    uv run --env-file .env pytest tests/integration/test_graph.py -v

YENTE_BASE_URL is pinned to the discard port (127.0.0.1:9) rather than left at
its private-VPC default. yente lives at 10.10.0.2:8000 on a VPC with no route
from this machine, so a real attempt would hang for the full 30s httpx timeout
per test. Port 9 refuses the connection immediately, so `screen_supplier`
fails fast and deterministically with `reachable=False` instead — this is not
a mistake, and it is not what proves PSC-I reachability (that is proven
separately in the deployed evidence run).

GOOGLE_CLOUD_LOCATION overrides .env's `us-central1` because gemini-3.6-flash
404s as a publisher model there; it is only served from the `global` Vertex AI
endpoint at the time of writing. This is a model-serving detail, independent
of the `us-central1` Agent Runtime deploy region.
"""

import json
import os
import uuid

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types

from app.agent import app
from app.state.commands import DONE, get_command
from app.state.firestore import get_client

pytestmark = pytest.mark.skipif(
    not os.environ.get("FRAPPE_API_KEY"),
    reason="FRAPPE_* credentials not in the environment",
)

# These write to whichever database FIRESTORE_DATABASE selects, because the graph
# nodes resolve their own client. Case IDs are TEST- prefixed and unique per run.
# Export FIRESTORE_DATABASE=keplaria-test to keep them out of the live database.


async def _run(event: dict) -> list:
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="graph-test"
    )
    outputs = []
    async for ev in runner.run_async(
        user_id="graph-test",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text=json.dumps(event))]
        ),
    ):
        if ev.output is not None:
            outputs.append(ev.output)
    return outputs


def _event(case_id: str, event_type: str) -> dict:
    return {
        "event_id": f"evt-{uuid.uuid4().hex[:8]}",
        "case_id": case_id,
        "event_type": event_type,
        "supplier": "Comercializadora Andes Verde SAS",
        "schema_version": 1,
    }


@pytest.mark.asyncio
async def test_new_supplier_packet_screens_and_creates_the_supplier():
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"

    outputs = await _run(_event(case_id, "new_supplier_packet"))

    # yente is unreachable from this machine (see module docstring), so this
    # only proves the screening node ran and recorded a structured result —
    # not that the sanctions check itself succeeded.
    screening = [o for o in outputs if isinstance(o, dict) and "reachable" in o]
    assert screening, "new_supplier_packet must engage compliance screening"
    assert "reachable" in screening[0]
    assert screening[0]["endpoint"]

    final = outputs[-1]
    assert final["status"] == "executed"
    assert final["external_id"] == "Comercializadora Andes Verde SAS"

    command = get_command(get_client(), case_id, "create_supplier")
    assert command["status"] == DONE


@pytest.mark.asyncio
async def test_certificate_received_skips_screening():
    """Two event types must take visibly different paths through the graph."""
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"

    outputs = await _run(_event(case_id, "certificate_received"))

    screening = [o for o in outputs if isinstance(o, dict) and "reachable" in o]
    assert not screening, "certificate_received must not engage compliance"
    assert outputs[-1]["status"] == "executed"


@pytest.mark.asyncio
async def test_replayed_case_does_not_write_the_supplier_twice():
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"
    await _run(_event(case_id, "new_supplier_packet"))

    outputs = await _run(_event(case_id, "new_supplier_packet"))

    assert outputs[-1]["status"] == "already_executed"
    assert get_command(get_client(), case_id, "create_supplier")["attempts"] == 1
