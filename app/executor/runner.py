"""Drives pending outbox commands against the ERP from outside the graph.

The graph (app.nodes.queue_supplier) only ever claims a command; it never
calls Frappe, because the Agent Runtime engine's PSC-I network attachment has
no public internet egress. This module does the actual write, and is run
from the ingress (ordinary Cloud Run, normal egress) — both right after a
successful engine invocation and, opportunistically, on every duplicate-event
redelivery, so a failed ERP write self-heals without spending the engine's
scarce quota.
"""

from __future__ import annotations

import httpx

from app.executor.frappe import FrappeError, create_or_update_supplier, frappe_client
from app.state.commands import DONE, record_failure, record_success
from app.state.firestore import CASES, OUTBOX

# The only action this executor knows how to run today. A command with any
# other action is left untouched rather than guessed at.
_CREATE_SUPPLIER = "create_supplier"


def execute_pending_commands(db, case_id: str) -> list[dict]:
    """Execute every not-yet-DONE outbox command for `case_id`.

    Idempotent by construction: a command already marked DONE is skipped,
    never re-driven — the same guarantee claim_command gives the graph side.
    Safe to call unconditionally on every ingress invocation, including a
    duplicate-event redelivery: a case whose commands are all DONE drains to
    a no-op.
    """
    outbox_ref = db.collection(CASES).document(case_id).collection(OUTBOX)
    results: list[dict] = []

    for snap in outbox_ref.stream():
        command = snap.to_dict() or {}
        action = command.get("action")

        if command.get("status") == DONE:
            continue
        if action != _CREATE_SUPPLIER:
            continue

        payload = command.get("payload") or {}
        supplier = payload.get("supplier_name", "")

        try:
            with frappe_client() as client:
                result = create_or_update_supplier(client, supplier)
        except (FrappeError, httpx.HTTPError) as exc:
            error = f"{type(exc).__name__}: {exc}"
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
