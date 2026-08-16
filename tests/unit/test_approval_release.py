"""The approval path, composed, on state the graph actually produces.

Every piece of this is tested in isolation elsewhere. This file exists because
the pieces were individually correct and jointly useless once before: an
approval commit that worked perfectly, an executor that honoured it perfectly,
and a park terminal that left an empty outbox for them to agree about. The
setup here therefore runs the real park_case rather than hand-writing the state
it leaves.

commit_approval does not drain, on purpose — it records a decision and grants
nothing. The composition below is what the approval UI's authenticated service
will do: commit, then drain if committed. That service does not exist yet, so
this file is where the claim "a human approval releases a real ERP write" is
substantiated.
"""

from __future__ import annotations

import contextlib

from app.executor import runner as runner_module
from app.executor.runner import execute_pending_commands
from app.nodes import park_case
from app.state.approvals import APPROVED, REJECTED, commit_approval
from app.state.commands import DONE, PENDING, get_command
from app.state.firestore import claim_event


class _StubContext:
    def __init__(self, state: dict):
        self.state = state


@contextlib.contextmanager
def _fake_client():
    yield object()


def _refuse_any_erp_write(supplier_name: str):
    """A create_supplier_if_absent stand-in that must never be called.

    Raises rather than recording, so a test that expects no ERP write fails
    loudly. The runner catches broad exceptions and records them as `failed`,
    so this surfaces as a status mismatch rather than an error — which the
    assertions below still catch.
    """

    def _explode(client, supplier, email_id=""):
        raise AssertionError(f"no ERP write may happen here (supplier={supplier})")

    return _explode


def _park_a_real_case(db, case_id: str) -> int:
    """Drive the actual claim-then-park path and return the case version.

    claim_event is what the ingress calls before any agent runs, and it is what
    puts case_version on the document — the value the reviewer's decision is
    taken against.
    """
    claim = claim_event(db, case_id, "EVT-1", {
        "event_type": "new_supplier_packet",
        "supplier": "Andes Foods",
    })
    ctx = _StubContext({
        "case": {
            "case_id": case_id,
            "event_type": "new_supplier_packet",
            "supplier": "Andes Foods",
            "effective_date": "2026-08-16",
        },
        "screening": {
            "endpoint": "http://10.10.0.2:8000", "supplier": "Andes Foods",
            "reachable": True, "error": None, "flagged": [],
            "candidates": [{"id": "syn-co-008", "score": 0.526, "match": False}],
        },
        "policy": {"policy_id": "supplier_risk", "policy_version": 2, "score": 0.25,
                   "band": "review", "factors_fired": [], "reasons": []},
    })
    park_case(None, ctx)
    return claim.case_version


def test_a_parked_case_refuses_until_it_is_approved(db, case_id, monkeypatch):
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module, "create_supplier_if_absent", _refuse_any_erp_write("Andes Foods")
    )

    _park_a_real_case(db, case_id)

    results = execute_pending_commands(db, case_id)

    assert [r["status"] for r in results] == ["refused_by_policy"]
    assert results[0]["gate_band"] == "review"
    assert results[0]["approval_id"] is None
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_an_approval_releases_the_parked_work(db, case_id, monkeypatch):
    created = []
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module, "create_supplier_if_absent",
        lambda client, supplier, email_id="": created.append(supplier)
        or {"external_id": supplier, "created": True},
    )

    version = _park_a_real_case(db, case_id)
    execute_pending_commands(db, case_id)

    result = commit_approval(db, case_id, "APR-1", version, APPROVED, "reviewer@example.com")
    assert result.committed is True, result.reason

    released = execute_pending_commands(db, case_id)

    assert [r["status"] for r in released] == ["done"]
    assert released[0]["approval_id"] == "APR-1"
    assert created == ["Andes Foods"]
    assert get_command(db, case_id, "create_supplier", 1)["status"] == DONE


def test_a_rejection_leaves_the_parked_work_unexecuted(db, case_id, monkeypatch):
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module, "create_supplier_if_absent", _refuse_any_erp_write("Andes Foods")
    )

    version = _park_a_real_case(db, case_id)

    assert commit_approval(
        db, case_id, "APR-1", version, REJECTED, "reviewer@example.com"
    ).committed is True

    results = execute_pending_commands(db, case_id)

    assert results[0]["status"] == "refused_by_policy"
    assert results[0]["band"] == "blocked"
    assert results[0]["gate_band"] == "review"
    assert get_command(db, case_id, "create_supplier", 1)["status"] == PENDING


def test_the_approval_is_replayable_without_a_second_erp_write(db, case_id, monkeypatch):
    """At-least-once delivery reaches this composition too. The approval is
    refused as a duplicate, and the drain is a no-op because the command is
    already DONE — two independent guards, both of which must hold."""
    created = []
    monkeypatch.setattr(runner_module, "frappe_client", _fake_client)
    monkeypatch.setattr(
        runner_module, "create_supplier_if_absent",
        lambda client, supplier, email_id="": created.append(supplier)
        or {"external_id": supplier, "created": True},
    )

    version = _park_a_real_case(db, case_id)
    commit_approval(db, case_id, "APR-1", version, APPROVED, "reviewer@example.com")
    execute_pending_commands(db, case_id)

    replay = commit_approval(db, case_id, "APR-1", version, APPROVED, "reviewer@example.com")
    execute_pending_commands(db, case_id)

    assert replay.committed is False
    assert replay.reason == "duplicate_approval"
    assert created == ["Andes Foods"], "the ERP write must happen exactly once"
