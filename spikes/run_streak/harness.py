"""Run the deployed judge run back to back and grade the streak.

Run:
    uv run --env-file .env python spikes/run_streak/harness.py --runs 10

The release criterion this exists for is **ten consecutive uninterrupted
deployed runs under 2:10, two of them cold starts**. It is serial, it cannot be
compressed, and it restarts from zero on any failure -- so the expensive
mistake is not a slow runner, it is an ATTENDED one. Ten browser approvals is an hour a human has to
sit through, and a failure at run nine spends that hour again. Everything
unusual in this file follows from making the streak unattended.

WHAT IS AND IS NOT EXERCISED
----------------------------
`console.review.decide` is reachable only behind `console.iap.require_reviewer`,
which verifies a JWT that IAP injects server-side and no script can produce
(there is deliberately no bypass in shipped code). So these runs do not click
Approve. They call the two functions that route calls once IAP has admitted a
reviewer -- `commit_approval` then `execute_pending_commands` -- which is the
whole of its domain behaviour and none of its transport. The evidence file says
this in a required field rather than a comment, because the streak is read at
release time by someone who will not open this module.

The browser path stays proven where it already is: `spikes/hitl_release` (a
real IAP sign-in releasing a real case) and the attended run in
`spikes/judge_run`.

WHAT "COLD" MEANS HERE
----------------------
The engine runs with `minInstances: 1`, so it never idles and no engine cold
start is available without changing its spec -- which would invalidate the warm
judge-run figure already measured and published. With auto-approval the review
service is never called either, so exactly one hop on the timed path can be
cold: `keplaria-ingress`, which has no minScale. A cold run therefore waits out
an idle gap and then READS `instance_count` to confirm the service was at zero.
A gap that was merely waited out proves nothing; see `streak.observed_cold`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

import google.auth  # noqa: E402
import httpx  # noqa: E402
from google.auth.transport.requests import Request as AuthRequest  # noqa: E402

from app.executor.runner import execute_pending_commands  # noqa: E402
from app.state.approvals import APPROVED, commit_approval  # noqa: E402
from app.state.firestore import CASES, get_client  # noqa: E402


def _load(name: str, path: Path):
    """Import a sibling spike module by path.

    These harnesses are scripts, not a package -- `spikes` has no __init__ and
    adding one would put deployment-time weight on directories whose whole
    point is to be throwaway-shaped. Same reason judge_run inserts the repo
    root on sys.path rather than being run with -m.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


streak = _load("run_streak_accounting", _HERE / "streak.py")
judge = _load("judge_run_harness", _HERE.parent / "judge_run" / "harness.py")

PROJECT = "keplaria"
REGION = "us-central1"
ENGINE_ID = "2127503872455868416"

# The only hop on the timed path that can be cold. The review service is not
# on it at all once approval is driven in-process, and the engine is pinned.
COLD_SERVICE = "keplaria-ingress"

# Deliberately not an address anyone could mistake for a reviewer. `.invalid`
# is reserved by RFC 2606 and can never resolve, so this string cannot be read
# later as a person who signed off on a supplier.
ACTOR = "unattended-streak-runner@keplaria.invalid"

EVIDENCE = _HERE / "evidence.json"


def log(msg: str) -> None:
    print(f"[streak] {msg}", flush=True)


def _token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(AuthRequest())
    return creds.token


# --- what the streak ran against ---------------------------------------------


def fingerprint() -> dict:
    """Identify the deployment these runs are measuring.

    The criterion says the streak must run against what ships, and "it did"
    is not a claim a reader can check three days later. Recording the engine's
    updateTime and the serving revision makes a redeploy mid-streak visible in
    the evidence instead of invisible in someone's memory of the week.
    """
    engine = {}
    try:
        response = httpx.get(
            f"https://{REGION}-aiplatform.googleapis.com/v1beta1/projects/"
            f"{PROJECT}/locations/{REGION}/reasoningEngines/{ENGINE_ID}",
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        spec = body.get("spec", {}).get("deploymentSpec", {})
        engine = {
            "resource": body.get("name"),
            "update_time": body.get("updateTime"),
            # Recorded because it is the reason only one hop can be cold. Note
            # the env block is NOT copied here: it carries the Frappe admin
            # key and secret in the clear, and this file is committed.
            "min_instances": spec.get("minInstances"),
            "max_instances": spec.get("maxInstances"),
        }
    except Exception as exc:  # noqa: BLE001 - a fingerprint is best-effort
        engine = {"error": f"{type(exc).__name__}: {exc}"}

    revision = subprocess.run(
        ["gcloud", "run", "services", "describe", COLD_SERVICE,
         f"--region={REGION}", f"--project={PROJECT}",
         "--format=value(status.latestReadyRevisionName)"],
        capture_output=True, text=True,
    ).stdout.strip()

    return {
        "engine": engine,
        "cold_service": COLD_SERVICE,
        "cold_service_revision": revision or None,
    }


# --- observing coldness -------------------------------------------------------


def instance_count(minutes: int = 5) -> list[dict] | None:
    """Recent `instance_count` points for the cold service, or None if unread.

    None on any failure, never an empty list: an empty list is the reading
    that MEANS scaled-to-zero (Cloud Run writes no series while a service has
    no instances), so collapsing a failed query into it would let a Monitoring
    outage manufacture a cold start. `streak.observed_cold` keeps the two
    apart; this function's only job is not to lose the distinction here.
    """
    now = datetime.now(timezone.utc)
    params = {
        "filter": (
            'metric.type="run.googleapis.com/container/instance_count" '
            f'AND resource.labels.service_name="{COLD_SERVICE}"'
        ),
        "interval.startTime": (now - timedelta(minutes=minutes)).isoformat(),
        "interval.endTime": now.isoformat(),
    }
    try:
        response = httpx.get(
            f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries",
            params=params,
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=60,
        )
        response.raise_for_status()
        points = []
        for series in response.json().get("timeSeries", []):
            for point in series.get("points", []):
                value = point.get("value", {})
                raw = value.get("int64Value", value.get("doubleValue", 0))
                points.append({"value": float(raw or 0),
                               "state": series.get("metric", {})
                                              .get("labels", {}).get("state")})
        return points
    except Exception as exc:  # noqa: BLE001
        log(f"  instance_count unreadable: {type(exc).__name__}: {exc}")
        return None


def go_cold(idle_minutes: int) -> tuple[bool, str]:
    """Idle long enough for the service to scale down, then confirm it did."""
    if idle_minutes:
        log(f"  cold-start slot: idling {idle_minutes} min for {COLD_SERVICE}")
        time.sleep(idle_minutes * 60)
    confirmed, why = streak.observed_cold(instance_count())
    log(f"  cold observation: {why}")
    return confirmed, why


# --- the unattended approval --------------------------------------------------


def auto_approve(db, case_id: str) -> float:
    """Approve `case_id` the way the review route does, minus its transport.

    Returns seconds spent, for symmetry with the human strategy it replaces.
    That number is reported and excluded from the budget exactly as the human
    wait is -- which is the point: what the streak times is unchanged by who
    or what pressed the button.

    The approval id is DERIVED from the case version rather than generated,
    because that is what `console.review.decide` does; generating one here
    would make the streak the only caller in the system that cannot be
    replayed idempotently.
    """
    t0 = time.time()
    case = (db.collection(CASES).document(case_id).get().to_dict() or {})
    version = int(case.get("case_version") or 0)
    # APPROVED, never the literal: the store accepts "approved" and refuses
    # anything else as `invalid_decision` -- a reason that reads like a parked
    # case or a version race rather than a typo in this line. It cost one live
    # run to learn that.
    result = commit_approval(db, case_id, f"{case_id}:v{version}", version,
                             APPROVED, ACTOR)
    if not result.committed:
        raise AssertionError(
            f"unattended approval refused for {case_id} at v{version}: "
            f"{result.reason}"
        )
    execute_pending_commands(db, case_id)
    return time.time() - t0


# --- the streak ---------------------------------------------------------------


def write_evidence(records: list[dict], started: str, deployment: dict,
                   **extra) -> None:
    """Rewrite the evidence file after EVERY run, not at the end.

    A streak is twenty-plus minutes of wall clock. Buffering the records until
    the loop finishes means a crash at run nine leaves nothing on disk, and
    "we ran nine and something died" is a materially different fact from "we
    never ran". Gate evidence lives in the repo for the same reason.
    """
    evidence = streak.build_evidence(records=records, started=started,
                                     deployment=deployment, **extra)
    EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=streak.REQUIRED_RUNS)
    parser.add_argument("--cold", type=int, default=streak.REQUIRED_COLD)
    parser.add_argument(
        "--idle-minutes", type=int, default=15,
        help="idle gap before a cold-start slot; 0 skips the wait (the run "
             "then only counts as cold if the service happened to be down)",
    )
    parser.add_argument(
        "--clean-supplier", default=judge.CLEAN_SUPPLIER,
        help="track B's supplier; see judge_run on why a stale ERP row matters",
    )
    args = parser.parse_args()

    db = get_client()
    started = datetime.now(timezone.utc).isoformat()
    deployment = fingerprint()
    log(f"engine update_time {deployment['engine'].get('update_time')} · "
        f"{COLD_SERVICE} {deployment['cold_service_revision']}")

    cold_slots = streak.cold_run_indices(total=args.runs, cold=args.cold)
    records: list[dict] = []
    broke_at = None

    for index in range(1, args.runs + 1):
        is_cold_slot = index in cold_slots
        log("")
        log(f"run {index}/{args.runs}{' (cold-start slot)' if is_cold_slot else ''}")

        if is_cold_slot:
            cold_confirmed, cold_why = go_cold(args.idle_minutes)
        else:
            cold_confirmed, cold_why = False, "warm run; not a cold-start slot"

        run = judge.run_once(db, approve=auto_approve,
                             clean_supplier=args.clean_supplier)
        machine = run["machine_a"] + run["machine_b"]
        record = {
            "run": index,
            "result": "FAIL" if run["failure"] else "PASS",
            "machine_seconds": round(machine, 1),
            "cold": is_cold_slot,
            "cold_confirmed": cold_confirmed,
            "cold_observation": cold_why,
            "approval_seconds": round(run["human_seconds"], 1),
            "case_ids": {"hitl": run["case_a"], "lifecycle": run["case_b"]},
            "failure": run["failure"],
            "steps": run["steps"],
        }
        records.append(record)
        write_evidence(records, started, deployment)

        kept = streak.run_counts(record)
        log(f"  {record['result']} in {machine:.1f}s "
            f"(budget {streak.BUDGET_SECONDS}s) -> "
            f"{'streak holds' if kept else 'STREAK BROKEN'}")
        if not kept:
            broke_at = index
            break

    verdict = streak.tally(records)
    extra = {}
    if verdict["green"]:
        # Once, at the end. Ten of these would cost twenty minutes and measure
        # the same repo ten times; and it is outside the timed budget anyway.
        extra["contract_suite"] = judge.suite_counts()
    if broke_at:
        extra["stopped_early"] = {
            "at_run": broke_at,
            "why": "a broken streak restarts from zero; continuing measures nothing",
        }
    write_evidence(records, started, deployment, **extra)

    log("")
    log(f"{verdict['consecutive']}/{verdict['required_runs']} consecutive · "
        f"{verdict['cold_confirmed']}/{verdict['required_cold']} observed cold "
        f"· {'GREEN' if verdict['green'] else 'NOT GREEN'} ({verdict['why']})")
    log(f"evidence -> {EVIDENCE}")
    return 0 if verdict["green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
