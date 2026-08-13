"""execute_pending_commands is what actually writes to the ERP now that the
graph only ever queues the command (see app.nodes.queue_supplier). Its
idempotency guarantee — a DONE command is never re-driven — is the whole
point of moving execution out of the graph without weakening the exactly-once
contract, so it has to be proven against the real Frappe Cloud site, not a
mock.

Run with:
    FIRESTORE_EMULATOR_HOST=localhost:8451 GOOGLE_CLOUD_PROJECT=keplaria \
    uv run --env-file .env pytest tests/integration/test_executor_runner.py -v
"""

import os
import uuid

import pytest

import app.executor.runner as runner_module
from app.executor.runner import execute_pending_commands
from app.state.commands import DONE, FAILED, claim_command, get_command, record_success

pytestmark = pytest.mark.skipif(
    not os.environ.get("FRAPPE_API_KEY"),
    reason="FRAPPE_* credentials not in the environment",
)


def _payload(name: str) -> dict:
    return {"supplier_name": name, "country": "Colombia"}


def test_pending_command_executes_and_reaches_done(db, case_id):
    supplier = f"TEST Supplier {uuid.uuid4().hex[:8]}"
    claim_command(db, case_id, "create_supplier", _payload(supplier))

    results = execute_pending_commands(db, case_id)

    assert results == [
        {
            "action": "create_supplier",
            "status": "done",
            "external_id": supplier,
            "created": True,
        }
    ]
    command = get_command(db, case_id, "create_supplier")
    assert command["status"] == DONE
    assert command["external_id"] == supplier


def test_done_command_is_skipped_not_reexecuted(db, case_id):
    supplier = f"TEST Supplier {uuid.uuid4().hex[:8]}"
    claim_command(db, case_id, "create_supplier", _payload(supplier))
    record_success(
        db, case_id, "create_supplier", supplier, {"external_id": supplier, "created": True}
    )

    # No real Frappe call happens here at all — the DONE check short-circuits
    # before frappe_client() is ever constructed, which is what proves the
    # command is genuinely skipped rather than re-driven with an idempotent
    # result on the far side.
    results = execute_pending_commands(db, case_id)

    assert results == [], "a DONE command must never be re-driven"
    assert get_command(db, case_id, "create_supplier")["status"] == DONE


def test_unexpected_exception_is_recorded_as_failed_not_left_pending(
    db, case_id, monkeypatch
):
    """Only FrappeError/httpx.HTTPError were originally caught, so a bug
    elsewhere in the response handling — e.g. a KeyError from a malformed
    Frappe reply — used to propagate uncaught, leaving the command PENDING
    with no record_failure call and no trace in the returned results. That
    made the failure invisible in the case document and the evidence until
    something happened to raise a caught type. This proves any exception
    type ends up recorded as `failed`, not silently PENDING. No real Frappe
    call is made — create_supplier_if_absent is monkeypatched to raise
    before any network I/O happens."""
    supplier = f"TEST Supplier {uuid.uuid4().hex[:8]}"
    claim_command(db, case_id, "create_supplier", _payload(supplier))

    def _boom(client, name):
        raise KeyError("data")

    monkeypatch.setattr(runner_module, "create_supplier_if_absent", _boom)

    results = execute_pending_commands(db, case_id)

    assert results == [
        {"action": "create_supplier", "status": "failed", "error": "KeyError: 'data'"}
    ]
    command = get_command(db, case_id, "create_supplier")
    assert command["status"] == FAILED
    assert "KeyError" in command["error"]
