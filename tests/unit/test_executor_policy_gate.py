"""Unit tests for the executor's refusal guard.

execute_pending_commands runs outside the graph, under the Cloud Run ingress
identity — a separate authorization boundary from the one the graph's
assess_risk branch enforces. These tests exercise only that guard: they never
reach a real Frappe call, because refusal happens before the ERP client is
ever constructed, so they belong here (hermetic, real Firestore, no live
external systems) rather than in tests/integration/test_executor_runner.py,
which is entirely pytest.mark.live and deselected by default.
"""

from __future__ import annotations


def test_executor_refuses_a_case_whose_verdict_is_not_clear(db, case_id):
    """Backstop at the authorization boundary: a command queued under older
    state must not drain once the case is blocked. Refusal is not failure —
    the command stays PENDING and is never marked DONE or FAILED."""
    from app.executor.runner import execute_pending_commands
    from app.state.commands import PENDING, claim_command, get_command
    from app.state.firestore import CASES

    claim_command(db, case_id, "create_supplier", 1, {"supplier_name": "Acme"})
    db.collection(CASES).document(case_id).set(
        {"policy": {"band": "blocked", "policy_version": 1}}, merge=True
    )

    results = execute_pending_commands(db, case_id)

    assert results == [
        {
            "action": "create_supplier",
            "status": "refused_by_policy",
            "band": "blocked",
            "policy_version": 1,
        }
    ]
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_executor_refuses_a_case_with_no_verdict_at_all(db, case_id):
    """Every graph path now writes a verdict, so absence is an anomaly."""
    from app.executor.runner import execute_pending_commands
    from app.state.commands import PENDING, claim_command, get_command

    claim_command(db, case_id, "create_supplier", 1, {"supplier_name": "Acme"})

    results = execute_pending_commands(db, case_id)

    assert results[0]["status"] == "refused_by_policy"
    assert results[0]["band"] is None
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING
