"""Streak accounting for the ten-consecutive-deployed-run criterion.

The runner itself publishes to a live Pub/Sub topic and waits on a deployed
engine, so nothing here touches that. What is tested is the part that decides
whether ten runs on disk actually satisfy the criterion -- because that is the
part a tired human reads at midnight and believes.

Two things it must never do. It must not let a run that completed but blew the
budget stay in the streak, and it must not count a cold-start run whose
coldness was never observed. Both would report green on a streak that is not.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.state.firestore import CASES

_PATH = Path(__file__).resolve().parents[2] / "spikes" / "run_streak" / "streak.py"
_SPEC = importlib.util.spec_from_file_location("run_streak_accounting", _PATH)
streak = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(streak)


def _run(index: int, *, result: str = "PASS", seconds: float = 61.5,
         cold: bool = False, cold_confirmed: bool = False) -> dict:
    return {
        "run": index,
        "result": result,
        "machine_seconds": seconds,
        "cold": cold,
        "cold_confirmed": cold_confirmed,
    }


def _ten_good() -> list[dict]:
    runs = [_run(1, cold=True, cold_confirmed=True),
            _run(2, cold=True, cold_confirmed=True)]
    runs += [_run(index) for index in range(3, 11)]
    return runs


def test_ten_passing_runs_with_two_confirmed_cold_starts_are_green():
    verdict = streak.tally(_ten_good())

    assert verdict["consecutive"] == 10
    assert verdict["cold_confirmed"] == 2
    assert verdict["green"] is True


def test_a_failed_run_restarts_the_streak_from_zero():
    verdict = streak.tally(_ten_good() + [_run(11, result="FAIL", seconds=44.0)])

    assert verdict["consecutive"] == 0
    assert verdict["green"] is False


def test_a_run_over_budget_breaks_the_streak_even_though_it_completed():
    runs = _ten_good()
    runs[5] = _run(6, result="PASS", seconds=131.0)

    verdict = streak.tally(runs)

    assert verdict["consecutive"] == 4
    assert verdict["green"] is False


def test_the_streak_resumes_counting_after_a_failure():
    runs = [_run(1), _run(2, result="FAIL")] + _ten_good()

    verdict = streak.tally(runs)

    assert verdict["consecutive"] == 10
    assert verdict["green"] is True


def test_an_unobserved_cold_start_does_not_count_toward_the_two():
    runs = _ten_good()
    runs[1] = _run(2, cold=True, cold_confirmed=False)

    verdict = streak.tally(runs)

    assert verdict["consecutive"] == 10
    assert verdict["cold_confirmed"] == 1
    assert verdict["green"] is False
    assert "cold" in verdict["why"]


def test_cold_starts_earned_before_a_failure_do_not_carry_across_it():
    runs = [_run(1, cold=True, cold_confirmed=True),
            _run(2, cold=True, cold_confirmed=True),
            _run(3, result="FAIL")]
    runs += [_run(index) for index in range(4, 14)]

    verdict = streak.tally(runs)

    assert verdict["consecutive"] == 10
    assert verdict["cold_confirmed"] == 0
    assert verdict["green"] is False


def test_the_verdict_records_the_thresholds_it_was_graded_against():
    verdict = streak.tally(_ten_good())

    assert verdict["required_runs"] == 10
    assert verdict["required_cold"] == 2
    assert verdict["budget_seconds"] == 130


def test_the_cold_runs_are_scheduled_first():
    assert streak.cold_run_indices(total=10, cold=2) == {1, 2}


# --- observing coldness ------------------------------------------------------
#
# The distinction these protect is between "the service reported zero
# instances" and "nobody could say". Only the first is evidence.


def test_no_instance_count_series_means_the_service_was_scaled_to_zero():
    cold, why = streak.observed_cold([])

    assert cold is True
    assert "no" in why.lower()


def test_a_series_of_zeroes_means_the_service_was_scaled_to_zero():
    cold, _why = streak.observed_cold([{"value": 0}, {"value": 0}])

    assert cold is True


def test_a_running_instance_means_the_run_was_not_cold():
    cold, why = streak.observed_cold([{"value": 0}, {"value": 1}])

    assert cold is False
    assert "1" in why


def test_an_unreadable_metric_is_not_evidence_of_coldness():
    cold, why = streak.observed_cold(None)

    assert cold is False
    assert "could not" in why.lower()


# --- evidence ----------------------------------------------------------------


def test_the_evidence_names_the_approval_path_the_streak_actually_used():
    evidence = streak.build_evidence(records=_ten_good(), started="2026-08-20T00:00:00Z",
                                     deployment={"engine_update_time": "x"})

    assert "commit_approval" in evidence["approval_path"]["driven_by"]
    assert evidence["approval_path"]["not_exercised"]


def test_the_evidence_carries_the_verdict_and_every_run():
    evidence = streak.build_evidence(records=_ten_good(), started="2026-08-20T00:00:00Z",
                                     deployment={"engine_update_time": "x"})

    assert evidence["verdict"]["green"] is True
    assert len(evidence["runs"]) == 10
    assert evidence["deployment"]["engine_update_time"] == "x"


# --- the seam in spikes/judge_run ---------------------------------------------


def _judge_run():
    path = Path(__file__).resolve().parents[2] / "spikes" / "judge_run" / "harness.py"
    spec = importlib.util.spec_from_file_location("judge_run_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_attended_judge_run_still_waits_for_a_human_by_default():
    """The day-10 gate evidence is an ATTENDED run.

    Making auto-approval the default here would keep writing
    spikes/judge_run/evidence.json while quietly removing the human from the
    beat that file is cited for.
    """
    module = _judge_run()

    assert module.default_approval() is module.wait_for_human_approval


# --- the unattended approval --------------------------------------------------


def _streak_harness():
    path = Path(__file__).resolve().parents[2] / "spikes" / "run_streak" / "harness.py"
    spec = importlib.util.spec_from_file_location("run_streak_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_unattended_approval_sends_a_decision_the_case_store_accepts(db, case_id):
    """The literal has to be the store's, not the runner's idea of it.

    `commit_approval` accepts "approved"; a runner that sends "approve" is
    refused as `invalid_decision`, which reads at a glance like a parked case
    or a version race rather than a typo in the caller. Seeded with an empty
    outbox so `execute_pending_commands` drains to a no-op and this test never
    reaches the live ERP.
    """
    from app.state.approvals import APPROVED

    db.collection(CASES).document(case_id).set({
        "case_id": case_id,
        "case_version": 2,
        "phase": "awaiting_approval",
        "policy": {"band": "review", "policy_version": 2},
    })

    _streak_harness().auto_approve(db, case_id)

    case = db.collection(CASES).document(case_id).get().to_dict()
    assert case["approval"]["decision"] == APPROVED


def test_the_evidence_says_what_the_budget_excludes():
    """`machine_seconds` is not the whole wall clock of a run.

    The approval beat is timed separately and left out of the budget, which
    matches how the attended baseline was measured -- but "excluded" is the
    kind of word that gets read as "zero". The file has to say it.
    """
    evidence = streak.build_evidence(records=_ten_good(), started="2026-08-20T00:00:00Z",
                                     deployment={})

    assert "approval_seconds" in evidence["budget_note"]
