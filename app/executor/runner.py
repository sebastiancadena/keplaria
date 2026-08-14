"""Drives pending outbox commands against the ERP from outside the graph.

The graph (app.nodes.queue_supplier) only ever claims a command; it never
calls Frappe, because the Agent Runtime engine's PSC-I network attachment has
no public internet egress. This module does the actual write, and is run
from the ingress (ordinary Cloud Run, normal egress) — right after a
successful engine invocation, and again, best-effort, on a duplicate-event
redelivery.

That redelivery path is a bounded repair attempt, not a standing
self-healing guarantee: the ingress always acks a duplicate_event with 200
regardless of whether the drain here succeeds (see ingress/main.py), so
Pub/Sub never redelivers that specific message again over a failed drain. A
persistently failing command (bad credentials, an ERP outage) gets exactly
one bonus attempt per delivery that happens to arrive — not an ongoing
retry loop — and stays `failed` until some unrelated later event for the
same case triggers another drain, or until a dedicated retry/DLQ path is
built (not yet).

This module also re-reads the gate's verdict (`cases/{case_id}.policy`) before
draining and refuses any command whose case is not `clear`. That is a backstop,
not the primary enforcement: the graph's assess_risk branch is what stops a
flagged supplier, and in the happy path this guard never fires, because the
review and blocked terminals claim no command. It exists for the anomalous
paths — a duplicate-event redelivery draining a command queued under older
state, or a graph-wiring bug — and it matters because this process runs under
a different identity (the Cloud Run ingress) than the graph.
"""

from __future__ import annotations

import httpx

from app.executor.frappe import FrappeError, create_supplier_if_absent, frappe_client
from app.risk import CLEAR
from app.state.commands import DONE, record_failure, record_success
from app.state.firestore import CASES, OUTBOX

# The only action this executor knows how to run today. A command with any
# other action is left untouched rather than guessed at.
_CREATE_SUPPLIER = "create_supplier"


def _policy_band(db, case_id: str) -> tuple[str | None, int | None]:
    """Read the gate's verdict off the case document.

    Returns (None, None) when the case or its policy block is absent — which
    every graph path now makes an anomaly, and which the caller refuses.
    """
    snap = db.collection(CASES).document(case_id).get()
    policy = ((snap.to_dict() or {}) if snap.exists else {}).get("policy") or {}
    return policy.get("band"), policy.get("policy_version")


def execute_pending_commands(db, case_id: str) -> list[dict]:
    """Execute every not-yet-DONE outbox command for `case_id`.

    Idempotent by construction: a command already marked DONE is skipped,
    never re-driven — the same guarantee claim_command gives the graph side.
    Safe to call unconditionally on every ingress invocation, including a
    duplicate-event redelivery: a case whose commands are all DONE drains to
    a no-op. Every failure of the ERP call itself — expected
    (FrappeError/httpx) or not — is recorded via record_failure before this
    returns, so a command is never left PENDING with no trace of what went
    wrong on that side.

    This does NOT cover a failure in record_success: that call sits outside
    the try block, on purpose, because it only runs after the ERP write
    already succeeded and there is nothing left to roll back. If Firestore
    itself is unreachable at that exact moment, the command stays PENDING
    despite the ERP record existing — a narrow, self-healing window: the
    next drain (any later event for the same case, or a duplicate-event
    redelivery) calls create_supplier_if_absent again, the ERP responds 409
    for the record that already exists, and that 409 path returns
    `created: False` without raising, so record_success runs and the command
    reaches DONE. It is not left permanently PENDING, just delayed to the
    next drain.
    """
    outbox_ref = db.collection(CASES).document(case_id).collection(OUTBOX)
    results: list[dict] = []

    band, policy_version = _policy_band(db, case_id)
    refused = band != CLEAR

    for snap in outbox_ref.stream():
        command = snap.to_dict() or {}
        action = command.get("action")

        if command.get("status") == DONE:
            continue
        if action != _CREATE_SUPPLIER:
            continue

        if refused:
            # Refusal-only: this guard can stop a write, never authorize one.
            # Deliberately NOT record_failure — a refusal is not a failure, and
            # the command must stay PENDING so that a later approval flipping
            # the verdict to `clear` lets the next drain execute it normally.
            results.append(
                {
                    "action": action,
                    "status": "refused_by_policy",
                    "band": band,
                    "policy_version": policy_version,
                }
            )
            continue

        payload = command.get("payload") or {}
        supplier = payload.get("supplier_name", "")

        try:
            with frappe_client() as client:
                result = create_supplier_if_absent(client, supplier)
        except (FrappeError, httpx.HTTPError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            record_failure(db, case_id, action, error)
            results.append({"action": action, "status": "failed", "error": error})
            continue
        except Exception as exc:
            # Not FrappeError/httpx.HTTPError — e.g. a malformed Frappe
            # response raising KeyError/TypeError out of
            # create_supplier_if_absent. Still a failed command, not a crash
            # this module should let propagate silently: leaving it PENDING
            # with no record_failure would make the failure invisible in the
            # case document and the evidence until some later drain happens
            # to hit a caught type. Record it the same way, then continue
            # draining the rest of the outbox rather than aborting on the
            # first unexpected error.
            error = f"{type(exc).__name__}: {exc}"[:300]
            record_failure(db, case_id, action, error)
            results.append({"action": action, "status": "failed", "error": error})
            continue

        record_success(db, case_id, action, result["external_id"], result)
        results.append(
            {
                "action": action,
                "status": "done",
                "external_id": result["external_id"],
                "created": result["created"],
            }
        )

    return results
