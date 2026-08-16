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

This module also re-reads the case before draining and refuses any
not-yet-executed PERMISSIVE command whose case is not `clear`. The band it
refuses on is an EFFECTIVE band, computed from two things: the gate's own
verdict (`cases/{case_id}.policy`) and a human decision
(`cases/{case_id}.approval`), when one applies. Approved clears, rejected
blocks, and the gate's own band is never overwritten — both bands are carried
into every record this module returns, so a reader can tell whether policy or
a person decided. An approval applies only while the case is still at the
version it was committed against; once a later event advances the case, the
approval silently stops applying and its commands go back to being refused.

For a `blocked` case this guard is a backstop rather than the primary
enforcement: assess_risk routes such a case to quarantine_case, which claims
nothing, so there is nothing here to refuse. It matters for the anomalous
paths — a duplicate-event redelivery draining a command queued under older
state, or a graph-wiring bug — and because this process runs under a different
identity (the Cloud Run ingress) than the graph.

For a `review` case the guard is the primary enforcement and fires constantly
by design. park_case claims the commands it parks, so a parked case drains to
`refused_by_policy` on every pass until a human approves it. That refusal is
the system working, not an anomaly, and it is what makes a parked case
releasable at all.

This executor-side guard is deliberately one-directional. It exists to stop
THIS PROCESS from GRANTING something — creating a supplier, attaching
evidence, releasing a hold, asking for a renewal — never to stop it
WITHHOLDING something. A hold (`apply_hold`) is restrictive: this guard
always lets it execute, regardless of the case's policy band, because
refusing to hold a risky supplier for scoring badly is exactly backwards.
`app.lifecycle.RESTRICTIVE` is the single source of truth for which actions
bypass this guard; every other known action is permissive and stays gated on
a `clear` verdict.

That one-directional promise covers only what reaches this module, not the
graph upstream of it, and what reaches it depends on the band:

- `blocked` → `quarantine_case`, which claims nothing. No command exists, so
  the graph withholds there by construction and `apply_hold` is never claimed
  for a case already deemed blocked.
- `review` → `park_case`, which claims what `decide()` names. If that includes
  `apply_hold`, this module executes it on the next drain, because a hold is
  RESTRICTIVE and bypasses the band guard. A held-because-overdue supplier
  whose case is also under review is therefore held now rather than waiting on
  the approval that gates the permissive commands beside it.

The gap that remains is narrower than it was but still real: a supplier who
becomes sanctioned scores `blocked`, and a blocked case still claims nothing,
so there is still no live capability to hold a newly-sanctioned supplier the
moment screening flags it. Closing that means letting the blocked terminal
claim restrictive commands, which is a separate decision and is NOT what the
review terminal's claiming implies.
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
from app.risk import BLOCKED, CLEAR
from app.state.approvals import APPROVED, REJECTED
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


def _version(value) -> int | None:
    """Total coercion of a case_version read out of a schemaless document.

    A version that arrives as a string or a float must not silently make an
    approval inapplicable — that failure mode looks exactly like a correctly
    refused stale approval, and would be near-impossible to diagnose from the
    outbox. An unparseable value returns None, which never compares equal, so
    the approval stops applying loudly rather than by accident of typing.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _effective_band(case: dict) -> tuple[str | None, str | None, str | None]:
    """Combine the gate's verdict with a human decision, if one applies.

    Returns (effective_band, gate_band, approval_id).

    An approval applies only while the case is still at the version it was
    committed against. That check exists here as well as in commit_approval
    because the two guard different moments: commit_approval refuses a decision
    taken about stale state, and this refuses a decision that has since GONE
    stale. Without it, an approval granted at version 4 would keep authorising
    writes after a later event advanced the case to 5 — approving what the
    reviewer never saw. A superseded approval simply stops applying, and the
    commands return to refused_by_policy rather than erroring.

    The gate's own band is returned unchanged alongside, and is never
    overwritten anywhere: the machine's conclusion is the auditable artefact,
    and a record that lost it could not answer "did policy or a person decide
    this?"
    """
    policy = case.get("policy") or {}
    gate_band = policy.get("band")

    approval = case.get("approval") or {}
    decision = approval.get("decision")
    approval_id = approval.get("approval_id")
    approved_at = _version(approval.get("case_version"))
    current = _version(case.get("case_version"))
    applies = (
        decision in (APPROVED, REJECTED)
        and approved_at is not None
        and approved_at == current
    )

    if not applies:
        return gate_band, gate_band, None
    if decision == APPROVED:
        return CLEAR, gate_band, approval_id
    return BLOCKED, gate_band, approval_id


def _policy_band(db, case_id: str) -> tuple[str | None, int | None, str | None, str | None]:
    """Read the case's effective and gate verdicts off the case document.

    Returns (effective_band, policy_version, gate_band, approval_id), all None
    when the case or its policy block is absent — which every graph path makes
    an anomaly, and which the caller refuses.
    """
    snap = db.collection(CASES).document(case_id).get()
    case = (snap.to_dict() or {}) if snap.exists else {}
    policy = case.get("policy") or {}
    effective, gate_band, approval_id = _effective_band(case)
    return effective, policy.get("policy_version"), gate_band, approval_id


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

    band, policy_version, gate_band, approval_id = _policy_band(db, case_id)
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
                    "gate_band": gate_band,
                    "approval_id": approval_id,
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
                "approval_id": approval_id,
            }
        )

    return results
