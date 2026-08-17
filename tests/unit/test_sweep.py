"""The sweep is the trigger the pipeline lacked, not a second executor.

NOTE ON INDEXES: these pass against the emulator, which does not enforce
collection-group indexes. Passing here does NOT prove the production index
exists — scripts/doctor.sh checks that separately.

NOTE ON `limit`: the `db` fixture is session-scoped over a SHARED database
(`keplaria-test`, or a persistent emulator), and a collection-group query sees
every case any other test ever left behind. Tests that assert a specific case
IS present therefore pass an explicit high `limit`; with the default 25 the
target can fall outside the cap and the test flakes for reasons that have
nothing to do with the code. The one test that checks the cap itself passes a
deliberately small `limit` instead.
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
    yield object()


def _clear_case(db, case_id: str) -> None:
    db.collection(CASES).document(case_id).set(
        {"policy": {"band": "clear", "policy_version": 2}}, merge=True
    )


def test_a_case_with_a_failed_command_is_found(db, case_id):
    from app.executor.sweep import find_stuck_case_ids

    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    case_ids, skipped = find_stuck_case_ids(db, limit=1000)

    assert case_id in case_ids
    assert skipped == 0


def test_a_case_whose_commands_are_dead_is_not_found(db, case_id):
    """A dead command must not keep the sweep busy forever."""
    from app.executor.sweep import find_stuck_case_ids

    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    for _ in range(MAX_EXECUTION_ATTEMPTS):
        record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    case_ids, _ = find_stuck_case_ids(db, limit=1000)

    assert case_id not in case_ids


def test_a_case_is_listed_once_however_many_commands_failed(db, case_id):
    from app.executor.sweep import find_stuck_case_ids

    for action in ("create_supplier", "apply_hold"):
        claim_command(db, case_id, action, 1, PAYLOAD)
        record_failure(db, case_id, action, 1, "HTTP 503")

    case_ids, _ = find_stuck_case_ids(db, limit=1000)

    assert case_ids.count(case_id) == 1, "the drain is per case, not per command"


def test_the_sweep_bound_is_respected_and_reported(db):
    """A silent cap reads as 'everything was covered'."""
    import uuid

    from app.executor.sweep import find_stuck_case_ids

    made = []
    for _ in range(3):
        cid = f"SWEEP-{uuid.uuid4().hex[:12]}"
        claim_command(db, cid, "create_supplier", 1, PAYLOAD)
        record_failure(db, cid, "create_supplier", 1, "HTTP 503")
        made.append(cid)

    case_ids, skipped = find_stuck_case_ids(db, limit=2)

    assert len(case_ids) == 2
    assert skipped >= 1, "the remainder must be counted, not dropped in silence"


def test_the_sweep_re_drives_a_stuck_command(db, case_id, monkeypatch):
    from app.executor.sweep import sweep_failed_commands

    calls = []
    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    monkeypatch.setattr(
        "app.executor.runner.create_supplier_if_absent",
        lambda *a, **k: calls.append("called")
        or {"external_id": "S1", "created": True},
    )
    _clear_case(db, case_id)
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    summary = sweep_failed_commands(db, limit=1000)

    assert "called" in calls, "the sweep must actually re-drive the command"
    assert summary["cases_swept"] >= 1
    assert case_id in summary["case_ids"]


def test_the_sweep_counts_a_command_that_dies_on_its_watch(db, case_id, monkeypatch):
    from app.executor.frappe import FrappeError
    from app.executor.sweep import sweep_failed_commands

    monkeypatch.setattr("app.executor.runner.frappe_client", _fake_client)
    monkeypatch.setattr(
        "app.executor.runner.create_supplier_if_absent",
        lambda *a, **k: (_ for _ in ()).throw(FrappeError("ERP down")),
    )
    _clear_case(db, case_id)
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    for _ in range(MAX_EXECUTION_ATTEMPTS - 1):
        record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    # limit=1000, matching the other case-presence tests above: the db
    # fixture is a shared database, and by the time this test runs the
    # emulator has accumulated cases from every earlier test run in the
    # session. Under the default MAX_CASES_PER_SWEEP cap this test's own
    # case can sort outside the swept set — its command is never driven,
    # commands_dead stays 0, and the test fails for reasons that have
    # nothing to do with the code under test.
    summary = sweep_failed_commands(db, limit=1000)

    assert summary["commands_dead"] >= 1
    # The aggregate count alone is exactly what pollution can fake: some
    # OTHER case's dead command satisfies ">= 1" even if this test's own
    # command never ran. Reading the command back and asserting ITS status
    # is what actually pins the behaviour this test claims to cover.
    command = get_command(db, case_id, "create_supplier", 1)
    assert command["status"] == DEAD


def test_a_refused_command_is_not_counted_as_driven(db, case_id, monkeypatch):
    """`commands_driven` must mean "the ERP call was attempted".

    A review-band case whose command failed while the case was still clear is
    refused on every sweep and never executes anything. Counting the result
    list wholesale reported a sweep across 25 such cases as 25 commands
    driven — a permanently stuck backlog disguised as work. The refusals get
    their own key so the condition stays visible instead.

    execute_pending_commands is stubbed rather than driven for real because
    the `db` fixture is a SHARED database: the sweep also visits whatever
    failed commands other tests left behind, and only a stub that refuses
    everything makes `commands_driven == 0` an exact assertion rather than a
    hopeful one.
    """
    from app.executor.sweep import sweep_failed_commands

    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    monkeypatch.setattr(
        "app.executor.sweep.execute_pending_commands",
        lambda _db, cid: [
            {"action": "create_supplier", "status": "refused_by_policy",
             "band": "review", "gate_band": "review", "approval_id": None}
        ],
    )

    summary = sweep_failed_commands(db, limit=1000)

    assert summary["cases_swept"] >= 1
    assert summary["commands_driven"] == 0, (
        "a refused command never reached the ERP and must not read as work"
    )
    assert summary["commands_refused"] == summary["cases_swept"]


def test_an_executed_command_is_still_counted_as_driven(db, case_id, monkeypatch):
    """The other half of the same claim: excluding refusals must not quietly
    stop counting the commands that did run."""
    from app.executor.sweep import sweep_failed_commands

    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    monkeypatch.setattr(
        "app.executor.sweep.execute_pending_commands",
        lambda _db, cid: [
            {"action": "create_supplier", "status": "done", "external_id": "S1"},
            {"action": "attach_evidence", "status": "failed", "error": "HTTP 503"},
            {"action": "clear_hold", "status": "refused_by_policy", "band": "review"},
        ],
    )

    summary = sweep_failed_commands(db, limit=1000)

    swept = summary["cases_swept"]
    assert swept >= 1
    assert summary["commands_driven"] == 2 * swept, (
        "a completed and a failed command both reached the ERP"
    )
    assert summary["commands_refused"] == swept


def test_one_broken_case_does_not_abort_the_sweep(db, monkeypatch):
    """The sweep runs unattended; one poisonous case must not stop the rest."""
    import uuid

    from app.executor.sweep import sweep_failed_commands

    good = f"SWEEP-{uuid.uuid4().hex[:12]}"
    bad = f"SWEEP-{uuid.uuid4().hex[:12]}"
    for cid in (bad, good):
        claim_command(db, cid, "create_supplier", 1, PAYLOAD)
        record_failure(db, cid, "create_supplier", 1, "HTTP 503")
        _clear_case(db, cid)

    driven = []

    def _drain(_db, cid):
        if cid == bad:
            raise RuntimeError("poison")
        driven.append(cid)
        return []

    monkeypatch.setattr("app.executor.sweep.execute_pending_commands", _drain)

    summary = sweep_failed_commands(db, limit=1000)

    assert good in driven, "a failing case must not abort the sweep"
    assert summary["cases_swept"] >= 1
