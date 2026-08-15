"""Drives pending outbox commands against the ERP from outside the graph.

The graph (app.nodes.commit_commands) only ever claims a command; it never
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
draining and refuses any not-yet-executed PERMISSIVE command whose case is not
`clear`. That is a backstop, not the primary enforcement: the graph's
assess_risk branch is what stops a flagged supplier, and in the happy path
this guard never fires, because the review and blocked terminals claim no
command. It exists for the anomalous paths — a duplicate-event redelivery
draining a command queued under older state, or a graph-wiring bug — and it
matters because this process runs under a different identity (the Cloud Run
ingress) than the graph.

The gate is deliberately one-directional. It exists to stop the system
GRANTING something — creating a supplier, attaching evidence, releasing a
hold, asking for a renewal — never to stop it WITHHOLDING something. A hold
(`apply_hold`) is restrictive: it always executes, regardless of the case's
policy band, because refusing to hold a risky supplier for scoring badly is
exactly backwards. `app.lifecycle.RESTRICTIVE` is the single source of truth
for which actions bypass the gate; every other known action is permissive and
stays gated on a `clear` verdict.
"""

from __future__ import annotations

import httpx

from app.executor.frappe import (
    PLACEHOLDER_CERTIFICATE_PDF,
    FrappeError,
    attach_evidence,
    clear_supplier_hold,
    create_supplier_if_absent,
    frappe_client,
    send_supplier_message,
    set_supplier_hold,
)
from app.lifecycle import (
    APPLY_HOLD,
    ATTACH_EVIDENCE,
    CLEAR_HOLD,
    CREATE_SUPPLIER,
    REQUEST_RENEWAL,
    RESTRICTIVE,
)
from app.risk import CLEAR
from app.state.commands import DONE, record_failure, record_success
from app.state.firestore import CASES, OUTBOX

# Drain order, not merely a lookup: attach_evidence must land before
# clear_hold, so the ERP never shows a released supplier whose evidence is
# still missing. outbox_ref.stream() guarantees no ordering of its own.
#
# Built from app.lifecycle's constants rather than re-spelled as literals:
# these are the same five names the decision function emits, and two
# spellings of one action-name set is exactly the drift the constants
# prevent.
_DRAIN_ORDER = (
    CREATE_SUPPLIER,
    ATTACH_EVIDENCE,
    REQUEST_RENEWAL,
    APPLY_HOLD,
    CLEAR_HOLD,
)


def _command_cycle(command: dict) -> int:
    """The command's cycle, defaulting to 1 for missing or unparseable values.

    Deliberately distinguishes absent from zero rather than using `or 1`: a
    command written before cycles existed has no `cycle` field and is
    genuinely cycle 1, but a stored `0` is data this function has no
    business silently rewriting.
    """
    raw = command.get("cycle")
    if raw is None:
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _run(action: str, client, payload: dict, cycle: int) -> dict:
    """Dispatch one claimed command to the matching Frappe call.

    `cycle` is the command's authoritative cycle — the same value already
    computed once per command in execute_pending_commands and used for
    record_success/record_failure — not re-derived from `payload`. Those
    two can drift (nothing enforces `payload["cycle"] == command["cycle"]`
    upstream), and attach_evidence uses this value to build the ERP-side
    idempotency filename (`{supplier}-cert-c{cycle}.pdf`); if this function
    read a different cycle than the ledger recorded, the command could reach
    `done` under one cycle while the attachment lands under another, and a
    later drain would silently attach a duplicate.
    """
    supplier = payload.get("supplier_name", "")
    if action == CREATE_SUPPLIER:
        return create_supplier_if_absent(client, supplier, email_id=payload.get("email_id", ""))
    if action == ATTACH_EVIDENCE:
        # PLACEHOLDER_CERTIFICATE_PDF, not an ad-hoc byte literal: the live
        # ERP runs a server-side pypdf content scan and rejects anything
        # that merely starts with "%PDF-1.4" without being a well-formed
        # stream. See its docstring in app/executor/frappe.py for why this
        # is a stand-in rather than the supplier's real certificate.
        return attach_evidence(client, supplier, cycle, PLACEHOLDER_CERTIFICATE_PDF)
    if action == REQUEST_RENEWAL:
        return send_supplier_message(
            client, supplier, "Certificate renewal required",
            f"Your certificate expires on {payload.get('expiry_date', 'the stated date')}. "
            "Please submit a renewed certificate.",
        )
    if action == APPLY_HOLD:
        return set_supplier_hold(client, supplier, payload.get("hold_type", "All"))
    if action == CLEAR_HOLD:
        return clear_supplier_hold(client, supplier)
    raise FrappeError(f"no handler for action {action!r}")


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

    commands = []
    for snap in outbox_ref.stream():
        command = snap.to_dict() or {}
        if command.get("status") == DONE:
            continue
        if command.get("action") not in _DRAIN_ORDER:
            # An action this executor does not know is left untouched
            # rather than guessed at or marked failed.
            continue
        commands.append(command)

    # Deterministic drain order, not stream() order: see _DRAIN_ORDER above.
    commands.sort(key=lambda c: _DRAIN_ORDER.index(c["action"]))

    for command in commands:
        action = command["action"]
        # Total coercion, not int() directly: `cycle` is read out of a
        # schemaless Firestore document, and a command that cannot be
        # parsed must not take the whole drain down with it. Cycle 1 is the
        # safe reading — it is what every pre-lifecycle command carries.
        cycle = _command_cycle(command)

        if refused and action not in RESTRICTIVE:
            # Refusal-only: this guard can stop a write, never authorize
            # one. RESTRICTIVE actions (a hold) bypass it entirely — the
            # gate exists to stop the system granting something, and
            # refusing to hold a risky supplier would invert that.
            # Deliberately NOT record_failure — a refusal is not a
            # failure, and the command must stay PENDING so that a later
            # approval flipping the verdict to `clear` lets the next drain
            # execute it normally.
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

        try:
            with frappe_client() as client:
                result = _run(action, client, payload, cycle)
        except (FrappeError, httpx.HTTPError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            record_failure(db, case_id, action, cycle, error)
            results.append({"action": action, "status": "failed", "error": error})
            continue
        except Exception as exc:
            # Not FrappeError/httpx.HTTPError — e.g. a malformed Frappe
            # response raising KeyError/TypeError out of an action
            # function. Still a failed command, not a crash this module
            # should let propagate silently: leaving it PENDING with no
            # record_failure would make the failure invisible in the case
            # document and the evidence until some later drain happens to
            # hit a caught type. Record it the same way, then continue
            # draining the rest of the outbox rather than aborting on the
            # first unexpected error.
            error = f"{type(exc).__name__}: {exc}"[:300]
            record_failure(db, case_id, action, cycle, error)
            results.append({"action": action, "status": "failed", "error": error})
            continue

        record_success(db, case_id, action, cycle, result["external_id"], result)
        results.append(
            {
                "action": action,
                "status": "done",
                "external_id": result["external_id"],
                "created": result["created"],
            }
        )

    return results
