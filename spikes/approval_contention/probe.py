"""Measure what concurrent approvals of the same case actually do.

Run:
    uv run --env-file .env python spikes/approval_contention/probe.py [workers] [rounds]

Writes to the `keplaria-test` database, never to `(default)`.

WHY THIS IS A PROBE AND NOT A TEST
----------------------------------
The unit tests in tests/unit/test_approvals.py drive `_commit_with_abort_retry`
through a fake transaction factory, which is the right way to pin the retry
CONTRACT deterministically. What they cannot do is tell you whether real
Firestore, on this database, under real threads, still hands an `Aborted` to
the caller. Only contention can answer that, and contention is not something
a test suite should depend on reproducing.

WHAT IT FOUND (2026-08-20)
--------------------------
Before `_commit_with_abort_retry` existed: **9 of 48** contended calls raised
`google.api_core.exceptions.Aborted` -- "409 Aborted due to cross-transaction
contention" -- out of `batch_get_documents`, i.e. from the READS inside the
transaction rather than from its commit. That distinction is the whole finding:
google-cloud-firestore's `@transactional` retry loop guards
`transaction._commit()` only, so a read-phase abort escapes on the first
attempt and no amount of `max_attempts` helps.

Exactly one commit won every round both before and after, so nothing was ever
double-approved. The damage was caller-visible: `console.review.decide` has no
handler for `Aborted`, so a reviewer double-clicking Approve could get a 500
where the code means to answer "already decided" -- and an unattended run
(spikes/run_streak) would record a failure the system did not actually have.

After the fix: **0 of 48**, every loser returning `duplicate_approval`.

Re-run this after any change to app/state/approvals.py. A rising count means
the retry has stopped covering the read phase.
"""

from __future__ import annotations

import os
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Set before app.state.firestore resolves a client. The probe writes real
# documents under real contention; the live database is not the place for that.
os.environ["FIRESTORE_DATABASE"] = "keplaria-test"

from app.state.approvals import APPROVED, commit_approval  # noqa: E402
from app.state.firestore import CASES, get_client  # noqa: E402


def main() -> int:
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    db = get_client(database="keplaria-test")

    raised = 0
    for index in range(rounds):
        case_id = f"TEST-CONTENTION-{uuid.uuid4().hex[:10]}"
        db.collection(CASES).document(case_id).set({
            "case_id": case_id,
            "case_version": 3,
            "phase": "awaiting_approval",
            "policy": {"band": "review", "policy_version": 2},
        })

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(commit_approval, db, case_id, "APR-RACE", 3,
                            APPROVED, "probe@keplaria.invalid")
                for _ in range(workers)
            ]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(("ok", future.result()))
                except BaseException as exc:  # noqa: BLE001 - the finding IS the exception
                    raised += 1
                    outcomes.append(("raised", type(exc).__name__))
                    if raised == 1:
                        print("=== first raised exception ===")
                        traceback.print_exception(type(exc), exc, exc.__traceback__)

        committed = sum(1 for kind, r in outcomes if kind == "ok" and r.committed)
        labels = [r if kind == "raised" else (r.reason or "COMMITTED")
                  for kind, r in outcomes]
        print(f"round {index}: committed={committed} outcomes={labels}")
        # Never negotiable, with or without the retry: the transaction exists
        # to make this true, so a probe that saw two would be reporting a far
        # worse finding than the one it was written for.
        if committed != 1:
            print(f"FAIL: {committed} commits won round {index}, expected exactly 1")
            return 1

    print(f"\nraised: {raised} / {workers * rounds}")
    return 0 if raised == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
