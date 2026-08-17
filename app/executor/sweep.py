"""Re-drives outbox commands that failed and were never retried.

The pipeline's only second chance at a failed command used to be an
unrelated later event for the same case happening to arrive, because the
ingress always acks a duplicate delivery regardless of whether its
opportunistic drain succeeded. This module supplies the missing trigger: a
Cloud Scheduler job calls POST /admin/sweep on the ingress, which calls
sweep_failed_commands here.

It is deliberately NOT a second executor. It finds cases and hands each to
the same app.executor.runner.execute_pending_commands the ingress uses, so
there is exactly one code path that talks to the ERP and one place where the
policy band is enforced. Everything this module adds is discovery and
bounding.

Bounded twice over, and both bounds matter:

- MAX_CASES_PER_SWEEP caps how much one run does, so a large backlog cannot
  hold the ingress' single request slot indefinitely. The remainder is
  logged and picked up by the next run, never silently dropped.
- The command ledger's MAX_EXECUTION_ATTEMPTS caps how many times any one
  command is ever tried. Without it this module would retry a permanently
  broken command every 15 minutes forever.

This is not self-healing. It re-drives transient failures; it does not
diagnose a persistently broken destination, and once a command is `dead` it
is skipped here like everywhere else.
"""

from __future__ import annotations

import logging

from google.cloud import firestore

from app.executor.runner import execute_pending_commands
from app.state.commands import DEAD, FAILED
from app.state.firestore import OUTBOX

logger = logging.getLogger("keplaria.sweep")

# One run's worth of work. Chosen to stay well inside a Cloud Run request
# while the ingress runs --concurrency=1 --max-instances=1, so a sweep never
# holds the only request slot long enough to matter to a real event push.
MAX_CASES_PER_SWEEP = 25


def find_stuck_case_ids(
    db: firestore.Client, limit: int = MAX_CASES_PER_SWEEP
) -> tuple[list[str], int]:
    """Case IDs holding at least one FAILED command, plus how many were skipped.

    A collection-group query, because the outbox is a subcollection under
    each case and there is no single collection to scan. Filtered on FAILED
    specifically: PENDING commands are either mid-flight or refused by
    policy (a review-band case refuses on every drain by design, and
    sweeping those would be a busy loop over cases correctly waiting for a
    human), and DEAD commands are terminal.

    Returns (case_ids, skipped) rather than truncating quietly — a silent cap
    reads as "everything was covered" to whoever reads the summary.
    """
    query = db.collection_group(OUTBOX).where(
        filter=firestore.FieldFilter("status", "==", FAILED)
    )

    case_ids: list[str] = []
    seen: set[str] = set()
    for snap in query.stream():
        case_id = (snap.to_dict() or {}).get("case_id")
        if not case_id or case_id in seen:
            continue
        seen.add(case_id)
        if len(case_ids) < limit:
            case_ids.append(case_id)

    skipped = len(seen) - len(case_ids)
    if skipped:
        logger.warning(
            "sweep bound at %s cases; %s more case(s) still hold a failed "
            "command and will wait for the next run",
            limit,
            skipped,
        )
    return case_ids, skipped


def sweep_failed_commands(
    db: firestore.Client, limit: int = MAX_CASES_PER_SWEEP
) -> dict:
    """Re-drive every stuck case found, and report what happened."""
    case_ids, skipped = find_stuck_case_ids(db, limit=limit)

    swept = 0
    driven = 0
    dead = 0
    for case_id in case_ids:
        try:
            results = execute_pending_commands(db, case_id)
        except Exception as exc:
            # This runs unattended on a schedule. One poisonous case must not
            # abort the rest of the sweep, so the failure is logged and the
            # loop continues — the case keeps its FAILED command and is found
            # again next run, still under the same attempt cap.
            logger.warning(
                "sweep failed to drain case %s: %s: %s",
                case_id,
                type(exc).__name__,
                str(exc)[:200],
            )
            continue

        swept += 1
        driven += len(results)
        dead += sum(1 for r in results if r.get("status") == DEAD)

    summary = {
        "cases_swept": swept,
        "commands_driven": driven,
        "commands_dead": dead,
        "cases_skipped": skipped,
        "case_ids": case_ids,
    }
    logger.info("sweep summary: %s", summary)
    return summary
