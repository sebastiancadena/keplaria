"""Streak accounting for the ten-consecutive-deployed-run criterion.

No I/O and no network: this grades run records and nothing else, which is why
it can be tested without a deployed engine on the other end.

Two rules here are the whole point of the module. A run that finished but blew
the 2:10 budget is a broken streak, not a slow success -- the criterion is
"ten consecutive runs UNDER 2:10", so a 131s run is a failure that happens to
have produced output. And a cold-start run only counts once its coldness was
OBSERVED; a run that was merely scheduled to be cold proves nothing about a
cold system, and the observation can fail for ordinary reasons (a stray
request warmed the service, the metric had not landed yet).

Both rules exist because the failure they prevent is silent. A streak reported
green is read once, believed, and then cited as a release criterion.
"""

from __future__ import annotations

BUDGET_SECONDS = 130
REQUIRED_RUNS = 10
REQUIRED_COLD = 2


def cold_run_indices(total: int, cold: int) -> set[int]:
    """Which 1-based run numbers get the cold-start treatment.

    The first `cold` of them. Position does not change what is measured -- each
    cold run needs its own idle gap either way, since the run before it leaves
    the service warm -- so the earliest slot is chosen for the one property
    that does differ: a streak that is going to break on a cold-start
    precondition breaks in the first twenty minutes rather than the last.
    """
    return set(range(1, min(cold, total) + 1))


def run_counts(record: dict) -> bool:
    """Did this run keep the streak alive?"""
    return (record.get("result") == "PASS"
            and float(record.get("machine_seconds") or 0) <= BUDGET_SECONDS)


def tally(records: list[dict],
          required_runs: int = REQUIRED_RUNS,
          required_cold: int = REQUIRED_COLD,
          budget_seconds: int = BUDGET_SECONDS) -> dict:
    """Grade a list of run records in the order they were run.

    Returns the thresholds it graded against alongside the counts. That is not
    decoration: this project has already shipped a gate whose threshold had
    drifted to whatever the suite happened to score, and a verdict that does
    not carry its own denominator cannot be checked by the person reading it.
    """
    consecutive = 0
    cold_confirmed = 0
    for record in records:
        if run_counts(record):
            consecutive += 1
            if record.get("cold_confirmed") is True:
                cold_confirmed += 1
        else:
            # Reset BOTH. A cold start observed before a failure was observed
            # on a streak that no longer exists.
            consecutive = 0
            cold_confirmed = 0

    reasons = []
    if consecutive < required_runs:
        reasons.append(f"{consecutive}/{required_runs} consecutive runs")
    if cold_confirmed < required_cold:
        reasons.append(f"{cold_confirmed}/{required_cold} observed cold starts")

    return {
        "consecutive": consecutive,
        "cold_confirmed": cold_confirmed,
        "green": not reasons,
        "why": "; ".join(reasons) if reasons else "criterion met",
        "required_runs": required_runs,
        "required_cold": required_cold,
        "budget_seconds": budget_seconds,
    }


def observed_cold(points: list[dict] | None) -> tuple[bool, str]:
    """Was the service actually at zero instances, or does nobody know?

    `points` is what a Cloud Monitoring read of
    `run.googleapis.com/container/instance_count` returned for the window
    before the run, or None if that read failed. The two are not the same
    answer and are deliberately not collapsed: a metric that could not be read
    is an absence of evidence, and counting it as a cold start would let an
    outage in Monitoring manufacture the two runs this criterion is hardest to
    earn.
    """
    if points is None:
        return False, "instance count could not be read; coldness unobserved"
    if not points:
        # Cloud Run writes no instance_count series while a service has no
        # instances, so an empty window IS the zero reading.
        return True, "no instance_count series in the window (scaled to zero)"
    peak = max(float(p.get("value") or 0) for p in points)
    if peak > 0:
        return False, f"{peak:g} instance(s) still running before the run"
    return True, "instance_count read zero across the window"


def build_evidence(records: list[dict], started: str, deployment: dict,
                   **extra) -> dict:
    """The streak's evidence file.

    `approval_path` is a required field rather than a comment. These runs do
    not click Approve in a browser -- they call the same two functions the
    review route calls once IAP has admitted a reviewer -- and a streak
    described as ten full judge runs without saying so would overstate what it
    proved. Naming what was NOT exercised is the part that has to survive
    someone skimming this file months later.
    """
    return {
        "captured_at": started,
        "what_this_is": (
            "Consecutive unattended rehearsals of the deployed judge run, "
            "graded against the ten-consecutive-runs-under-2:10 criterion."
        ),
        "budget_note": (
            "`machine_seconds` is the timed total each run is graded against "
            "and EXCLUDES `approval_seconds`, which is reported per run "
            "alongside it. This matches how the attended baseline was "
            "measured -- there the same work sat inside a human's think time "
            "-- so the two are comparable. It is not zero; add the two fields "
            "for a run's full wall clock."
        ),
        "approval_path": {
            "driven_by": "console.store.commit_approval + "
                         "app.executor.runner.execute_pending_commands",
            "not_exercised": [
                "IAP identity assertion (console.iap.require_reviewer)",
                "the review form's cross-site refusal",
                "the review templates",
                "the keplaria-review service itself",
            ],
            "why": (
                "require_reviewer verifies an assertion IAP injects "
                "server-side, which no script can produce. The browser "
                "approval path is proven separately by spikes/hitl_release "
                "and by the attended judge run in spikes/judge_run."
            ),
        },
        "deployment": deployment,
        "verdict": tally(records),
        "runs": records,
        **extra,
    }
