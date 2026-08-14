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
from app.state.commands import DONE, PENDING, get_command, record_success
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
    """The graph stops at queueing — see app.nodes.queue_supplier: the Agent
    Runtime engine has no public internet path to Frappe Cloud, so ERP
    execution happens outside the graph (app.executor.runner, driven by the
    ingress). No Frappe call is made from this test."""
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
    assert final["status"] == "command_queued"
    assert final["case_id"] == case_id

    command = get_command(get_client(), case_id, "create_supplier")
    assert command["status"] == PENDING
    assert command["payload"]["supplier_name"] == "Comercializadora Andes Verde SAS"


@pytest.mark.asyncio
async def test_certificate_received_skips_screening():
    """Two event types must take visibly different paths through the graph."""
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"

    outputs = await _run(_event(case_id, "certificate_received"))

    screening = [o for o in outputs if isinstance(o, dict) and "reachable" in o]
    assert not screening, "certificate_received must not engage compliance"
    assert outputs[-1]["status"] == "command_queued"


@pytest.mark.asyncio
async def test_replayed_case_does_not_reclaim_a_done_command():
    """The graph's own idempotency guarantee: once a command is DONE — which,
    since queue_supplier never calls Frappe, only ever happens via
    app.executor.runner.execute_pending_commands running outside the graph —
    running the graph again for the same case must report 'already_executed'
    without re-claiming (bumping attempts on) the command."""
    case_id = f"TEST-{uuid.uuid4().hex[:12]}"
    db = get_client()

    first = await _run(_event(case_id, "new_supplier_packet"))
    assert first[-1]["status"] == "command_queued"
    assert get_command(db, case_id, "create_supplier")["status"] == PENDING

    # Simulate the ingress's out-of-band executor completing the command.
    record_success(
        db,
        case_id,
        "create_supplier",
        "Comercializadora Andes Verde SAS",
        {"external_id": "Comercializadora Andes Verde SAS", "created": True},
    )

    second = await _run(_event(case_id, "new_supplier_packet"))

    assert second[-1]["status"] == "already_executed"
    command = get_command(db, case_id, "create_supplier")
    assert command["status"] == DONE
    assert command["attempts"] == 1
