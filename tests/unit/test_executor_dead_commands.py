"""A dead command is skipped by the drain, and reported rather than hidden.

Hermetic: the ERP client is monkeypatched, so these belong here and not in
tests/integration/test_executor_runner.py, which is entirely
pytest.mark.live and deselected by default.
"""

from __future__ import annotations

import contextlib

from app.state.commands import (
    DEAD,
    MAX_EXECUTION_ATTEMPTS,
    claim_command,
    get_command,
    record_failure,
)
from app.state.firestore import CASES

PAYLOAD = {"supplier_name": "Andes Verde Import Export SAS", "country": "Colombia"}


@contextlib.contextmanager
def _fake_client():
    """Stands in for frappe_client(); the runner opens it as a context manager."""
    yield object()


def _clear_case(db, case_id: str) -> None:
    db.collection(CASES).document(case_id).set(
        {"policy": {"band": "clear", "policy_version": 2}}, merge=True
    )


def _kill(db, case_id: str, action: str, cycle: int = 1) -> None:
    for _ in range(MAX_EXECUTION_ATTEMPTS):
        record_failure(db, case_id, action, cycle, "HTTP 503")


def test_a_dead_command_is_not_re_driven(db, case_id, monkeypatch):
    from app.executor.runner import execute_pending_commands

    calls = []
    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    monkeypatch.setattr(
        "app.executor.runner.create_supplier_if_absent",
        lambda *a, **k: calls.append("called")
        or {"external_id": "S1", "created": True},
    )
    _clear_case(db, case_id)
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    _kill(db, case_id, "create_supplier")

    execute_pending_commands(db, case_id)

    assert calls == [], "a dead command must never reach the ERP again"


def test_a_dead_command_is_reported_not_silently_skipped(db, case_id, monkeypatch):
    from app.executor.runner import execute_pending_commands

    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    _clear_case(db, case_id)
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    _kill(db, case_id, "create_supplier")

    results = execute_pending_commands(db, case_id)

    assert results == [
        {"action": "create_supplier", "status": DEAD, "error": "HTTP 503"}
    ], "a dead command dropped from the report is indistinguishable from no work"


def test_a_dead_command_does_not_stop_its_siblings(db, case_id, monkeypatch):
    from app.executor.runner import execute_pending_commands

    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    monkeypatch.setattr(
        "app.executor.runner.set_supplier_hold",
        lambda client, supplier_name, hold_type="All": {
            "external_id": supplier_name,
            "created": True,
        },
    )
    _clear_case(db, case_id)
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    _kill(db, case_id, "create_supplier")
    claim_command(db, case_id, "apply_hold", 1, PAYLOAD)

    results = execute_pending_commands(db, case_id)

    by_action = {r["action"]: r["status"] for r in results}
    assert by_action["apply_hold"] == "done"
    assert by_action["create_supplier"] == DEAD


def test_the_drain_marks_a_command_dead_at_the_cap(db, case_id, monkeypatch):
    """End to end through the drain rather than by calling record_failure:
    proves the executor's own failure path reaches the terminal state."""
    from app.executor.frappe import FrappeError
    from app.executor.runner import execute_pending_commands

    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)

    def _boom(*a, **k):
        raise FrappeError("ERP down")

    monkeypatch.setattr("app.executor.runner.create_supplier_if_absent", _boom)
    _clear_case(db, case_id)

    for attempt in range(MAX_EXECUTION_ATTEMPTS):
        claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
        results = execute_pending_commands(db, case_id)
        expected = DEAD if attempt == MAX_EXECUTION_ATTEMPTS - 1 else "failed"
        assert results[0]["status"] == expected, f"attempt {attempt + 1}"

    assert get_command(db, case_id, "create_supplier", 1)["status"] == DEAD
