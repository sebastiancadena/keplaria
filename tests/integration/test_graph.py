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
from app.state.commands import get_command
from app.state.firestore import get_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("FRAPPE_API_KEY"),
        reason="FRAPPE_* credentials not in the environment",
    ),
]

# These write to whichever database FIRESTORE_DATABASE selects, because the graph
# nodes resolve their own client. Case IDs are TEST- prefixed and unique per run.
# tests/conftest.py forces FIRESTORE_DATABASE=keplaria-test, so a plain run can
# never land in the live "(default)" database.


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
async def test_new_supplier_packet_screens_and_queues_the_command():
    """yente is unreachable from this machine (see module docstring), so
    screening comes back `reachable=False`. Under the risk gate that fires
    SCREENING_UNAVAILABLE and lands in `review`, which routes to park_case —
    not queue_supplier. This pins the deliberate, fail-closed behavior: an
    unreachable screening service must park the case for a human, not queue
    an ERP write and not claim any command."""
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"

    outputs = await _run(_event(case_id, "new_supplier_packet"))

    # This only proves the screening node ran and recorded a structured
    # result — not that the sanctions check itself succeeded.
    screening = [o for o in outputs if isinstance(o, dict) and "reachable" in o]
    assert screening, "new_supplier_packet must engage compliance screening"
    assert "reachable" in screening[0]
    assert screening[0]["endpoint"]

    final = outputs[-1]
    assert final["status"] == "awaiting_approval"
    assert final["case_id"] == case_id
    assert final["policy"]["band"] == "review"

    command = get_command(get_client(), case_id, "create_supplier", 1)
    assert command is None, "an unreachable screening service must claim no command"


@pytest.mark.asyncio
async def test_certificate_received_skips_screening():
    """Two event types must take visibly different paths through the graph."""
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"

    outputs = await _run(_event(case_id, "certificate_received"))

    screening = [o for o in outputs if isinstance(o, dict) and "reachable" in o]
    assert not screening, "certificate_received must not engage compliance"
    assert outputs[-1]["status"] == "no_action"


@pytest.mark.asyncio
async def test_replayed_case_does_not_reclaim_a_done_command():
    """Same fail-closed gate as the previous test: yente is unreachable from
    this machine, so every event for this case lands in `review` and parks
    at park_case rather than ever reaching queue_supplier — there is no DONE
    command here for a replay to reclaim. What this proves instead is the
    review branch's own replay idempotency: running the identical event
    twice for the same case must park it both times and must never claim a
    command on either run — a graph-wiring bug that let a replay slip past
    park_case into queue_supplier would show up here as a stray command."""
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"
    db = get_client()

    first = await _run(_event(case_id, "new_supplier_packet"))
    assert first[-1]["status"] == "awaiting_approval"
    assert get_command(db, case_id, "create_supplier", 1) is None

    second = await _run(_event(case_id, "new_supplier_packet"))

    assert second[-1]["status"] == "awaiting_approval"
    assert get_command(db, case_id, "create_supplier", 1) is None
