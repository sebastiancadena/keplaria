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

import contextlib

from app.state.commands import claim_command


@contextlib.contextmanager
def _fake_client():
    """Stands in for frappe_client(). Never used — every action function that
    would touch it is monkeypatched in these tests — but the runner opens it
    as a context manager, so it must be one."""
    yield object()


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
            "gate_band": "blocked",
            "approval_id": None,
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


def test_a_hold_executes_even_when_the_case_is_blocked(db, case_id, monkeypatch):
    from app.executor.runner import execute_pending_commands

    calls = []
    monkeypatch.setattr(
        "app.executor.runner.set_supplier_hold",
        lambda client, supplier_name, hold_type="All": calls.append(supplier_name)
        or {"external_id": supplier_name, "created": True},
    )
    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    db.collection("cases").document(case_id).set({"policy": {"band": "blocked"}})
    claim_command(db, case_id, "apply_hold", 1, {"supplier_name": "Andes"})

    results = execute_pending_commands(db, case_id)

    assert calls == ["Andes"], (
        "a hold is restrictive; refusing it because the case is blocked would "
        "invert the gate's purpose"
    )
    assert results[0]["status"] == "done"


def test_a_hold_release_is_refused_when_the_case_is_blocked(db, case_id, monkeypatch):
    from app.executor.runner import execute_pending_commands

    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    db.collection("cases").document(case_id).set({"policy": {"band": "blocked"}})
    claim_command(db, case_id, "clear_hold", 2, {"supplier_name": "Andes"})

    results = execute_pending_commands(db, case_id)

    assert results[0]["status"] == "refused_by_policy", (
        "releasing a hold grants something, so it stays gated"
    )


def test_evidence_is_attached_before_the_hold_is_released(db, case_id, monkeypatch):
    from app.executor.runner import execute_pending_commands

    order = []
    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    monkeypatch.setattr(
        "app.executor.runner.attach_evidence",
        lambda *a, **k: order.append("attach") or {"external_id": "F1", "created": True},
    )
    monkeypatch.setattr(
        "app.executor.runner.clear_supplier_hold",
        lambda *a, **k: order.append("clear") or {"external_id": "S1", "created": True},
    )
    db.collection("cases").document(case_id).set({"policy": {"band": "clear"}})
    claim_command(db, case_id, "clear_hold", 2, {"supplier_name": "Andes"})
    claim_command(db, case_id, "attach_evidence", 2, {"supplier_name": "Andes",
                                                      "cycle": 2})

    execute_pending_commands(db, case_id)

    assert order == ["attach", "clear"], (
        "the ERP must never show a released supplier whose evidence has not landed"
    )


def test_attach_evidence_uses_the_commands_cycle_not_the_payloads(
    db, case_id, monkeypatch
):
    """The ledger (record_success/record_failure) and the ERP-side
    idempotency filename (`{supplier}-cert-c{cycle}.pdf`) must agree on
    which cycle a command belongs to. attach_evidence must be called with
    the command's own top-level `cycle` — the same value used for
    record_success — never a `cycle` that happens to also live inside the
    payload dict, because nothing upstream enforces the two stay equal."""
    from app.executor.runner import execute_pending_commands

    seen_cycles = []
    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    monkeypatch.setattr(
        "app.executor.runner.attach_evidence",
        lambda client, supplier_name, cycle, content: seen_cycles.append(cycle)
        or {"external_id": "F1", "created": True},
    )
    db.collection("cases").document(case_id).set({"policy": {"band": "clear"}})
    # Top-level cycle is 5; the payload's own "cycle" field disagrees (99).
    claim_command(
        db, case_id, "attach_evidence", 5,
        {"supplier_name": "Andes", "cycle": 99},
    )

    execute_pending_commands(db, case_id)

    assert seen_cycles == [5], (
        "attach_evidence must receive the command's ledger cycle, not the "
        "payload's, or the ERP attachment filename can diverge from the "
        "cycle Firestore recorded as done"
    )


def test_an_unknown_action_is_left_untouched(db, case_id, monkeypatch):
    from app.executor.runner import execute_pending_commands

    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    db.collection("cases").document(case_id).set({"policy": {"band": "clear"}})
    claim_command(db, case_id, "launch_rocket", 1, {})

    assert execute_pending_commands(db, case_id) == []


def test_an_approval_releases_a_command_the_gate_refused(db, case_id, monkeypatch):
    """The path that makes a parked case finishable at all. Before this, a
    review verdict refused the command forever and nothing could release it."""
    from app.executor import runner as runner_module
    from app.executor.runner import execute_pending_commands
    from app.state.approvals import APPROVED, commit_approval
    from app.state.commands import DONE, claim_command, get_command
    from app.state.firestore import CASES

    calls = []
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module, "create_supplier_if_absent",
        lambda client, supplier, email_id="": calls.append(supplier)
        or {"external_id": supplier, "created": True},
    )

    claim_command(db, case_id, "create_supplier", 1, {"supplier_name": "Acme"})
    db.collection(CASES).document(case_id).set({
        "case_id": case_id, "case_version": 3, "phase": "awaiting_approval",
        "policy": {"band": "review", "policy_version": 2},
    }, merge=True)
    commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")

    results = execute_pending_commands(db, case_id)

    assert results[0]["status"] == "done"
    assert calls == ["Acme"]
    assert get_command(db, case_id, "create_supplier", 1)["status"] == DONE


def test_a_superseded_approval_no_longer_releases_anything(db, case_id):
    """The staleness contract, enforced a second time at read time rather than
    only at commit time. An approval granted at version 3 must stop applying
    once a later event advances the case to 4 — otherwise it authorises writes
    for state the reviewer never saw."""
    from app.executor.runner import execute_pending_commands
    from app.state.approvals import APPROVED, commit_approval
    from app.state.commands import PENDING, claim_command, get_command
    from app.state.firestore import CASES

    claim_command(db, case_id, "create_supplier", 1, {"supplier_name": "Acme"})
    db.collection(CASES).document(case_id).set({
        "case_id": case_id, "case_version": 3, "phase": "awaiting_approval",
        "policy": {"band": "review", "policy_version": 2},
    }, merge=True)
    commit_approval(db, case_id, "APR-1", 3, APPROVED, "reviewer@example.com")
    db.collection(CASES).document(case_id).set({"case_version": 4}, merge=True)

    results = execute_pending_commands(db, case_id)

    assert results[0]["status"] == "refused_by_policy"
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_a_rejection_refuses_a_command_the_gate_had_cleared(db, case_id):
    """One-directional in the other direction too: a human may withhold what
    the machine would have granted."""
    from app.executor.runner import execute_pending_commands
    from app.state.approvals import REJECTED, commit_approval
    from app.state.commands import PENDING, claim_command, get_command
    from app.state.firestore import CASES

    claim_command(db, case_id, "create_supplier", 1, {"supplier_name": "Acme"})
    db.collection(CASES).document(case_id).set({
        "case_id": case_id, "case_version": 3, "phase": "awaiting_approval",
        "policy": {"band": "clear", "policy_version": 2},
    }, merge=True)
    commit_approval(db, case_id, "APR-1", 3, REJECTED, "reviewer@example.com")

    results = execute_pending_commands(db, case_id)

    assert results[0]["status"] == "refused_by_policy"
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_the_refusal_record_names_the_machine_verdict_and_the_approval(db, case_id):
    """A reader of the outbox must be able to tell 'the gate refused this' from
    'a human refused this'."""
    from app.executor.runner import execute_pending_commands
    from app.state.approvals import REJECTED, commit_approval
    from app.state.commands import claim_command
    from app.state.firestore import CASES

    claim_command(db, case_id, "create_supplier", 1, {"supplier_name": "Acme"})
    db.collection(CASES).document(case_id).set({
        "case_id": case_id, "case_version": 3, "phase": "awaiting_approval",
        "policy": {"band": "clear", "policy_version": 2},
    }, merge=True)
    commit_approval(db, case_id, "APR-1", 3, REJECTED, "reviewer@example.com")

    results = execute_pending_commands(db, case_id)

    assert results[0]["gate_band"] == "clear"
    assert results[0]["band"] == "blocked"
    assert results[0]["approval_id"] == "APR-1"


def test_a_hold_still_executes_regardless_of_any_approval(db, case_id, monkeypatch):
    """RESTRICTIVE actions bypass the guard entirely, and adding approvals must
    not quietly make a hold conditional on one."""
    from app.executor import runner as runner_module
    from app.executor.runner import execute_pending_commands
    from app.state.approvals import REJECTED, commit_approval
    from app.state.commands import claim_command
    from app.state.firestore import CASES

    held = []
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module, "set_supplier_hold",
        lambda client, supplier, hold_type: held.append(supplier)
        or {"external_id": supplier, "created": True},
    )

    claim_command(db, case_id, "apply_hold", 1, {"supplier_name": "Acme", "hold_type": "All"})
    db.collection(CASES).document(case_id).set({
        "case_id": case_id, "case_version": 3, "phase": "awaiting_approval",
        "policy": {"band": "clear", "policy_version": 2},
    }, merge=True)
    commit_approval(db, case_id, "APR-1", 3, REJECTED, "reviewer@example.com")

    execute_pending_commands(db, case_id)

    assert held == ["Acme"]


def test_a_rejected_review_case_keeps_its_hold_and_loses_everything_else(
    db, case_id, monkeypatch
):
    """The parked-case shape, which the tests above never build.

    Every other rejection test here sets the gate band to `clear` and lets the
    rejection supply the refusal. That is the duplicate-redelivery anomaly, not
    the path a human actually meets: a real reviewer only ever sees a `review`
    case, whose permissive commands were ALREADY refused before they arrived,
    and whose restrictive command has ALREADY executed. What the rejection
    changes is not whether the permissive commands run — they never could —
    but who is on record for refusing them.

    Both halves matter and they pull in opposite directions, which is why they
    are asserted together: a change that made RESTRICTIVE respect the human
    decision would still pass every other test in this file.
    """
    from app.executor import runner as runner_module
    from app.executor.runner import execute_pending_commands
    from app.state.approvals import REJECTED, commit_approval
    from app.state.commands import PENDING, claim_command, get_command
    from app.state.firestore import CASES

    held = []
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module, "set_supplier_hold",
        lambda client, supplier, hold_type: held.append(supplier)
        or {"external_id": supplier, "created": True},
    )

    claim_command(db, case_id, "create_supplier", 1, {"supplier_name": "Acme"})
    claim_command(db, case_id, "apply_hold", 1, {"supplier_name": "Acme", "hold_type": "All"})
    db.collection(CASES).document(case_id).set({
        "case_id": case_id, "case_version": 3, "phase": "awaiting_approval",
        "policy": {"band": "review", "policy_version": 2},
    }, merge=True)
    commit_approval(db, case_id, "APR-1", 3, REJECTED, "reviewer@example.com")

    results = execute_pending_commands(db, case_id)
    by_action = {r["action"]: r for r in results}

    # The restriction ran despite the rejection...
    assert held == ["Acme"]
    # ...and the permissive command is refused with the human named, not
    # merely refused. gate_band survives so the record can still answer "what
    # did the machine think?" next to "what did the person decide?".
    assert by_action["create_supplier"]["status"] == "refused_by_policy"
    assert by_action["create_supplier"]["gate_band"] == "review"
    assert by_action["create_supplier"]["band"] == "blocked"
    assert by_action["create_supplier"]["approval_id"] == "APR-1"
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING
