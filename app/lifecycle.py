"""Deterministic station-keeping transitions.

`decide` is a pure, total function: no I/O, no clock of its own, never
raises. The current date always arrives on the event as `effective_date`,
which is what lets a simulated year run in seconds and a real scheduler
produce the same events later without changing a rule here.

Every refusal returns a reason code rather than an exception, so the case
document always records why nothing happened — "no commands" and "we did not
look" must never be indistinguishable.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from pydantic import BaseModel

from app.risk import LifecycleTiming

ONBOARDING = "onboarding"
ACTIVE = "active"
RENEWAL_REQUESTED = "renewal_requested"
HELD = "held"
QUARANTINED = "quarantined"

CREATE_SUPPLIER = "create_supplier"
ATTACH_EVIDENCE = "attach_evidence"
REQUEST_RENEWAL = "request_renewal"
APPLY_HOLD = "apply_hold"
CLEAR_HOLD = "clear_hold"

# Actions the policy gate may never withhold. The gate exists to stop the
# system granting something; refusing to apply a hold because the supplier
# scored badly would invert its purpose.
RESTRICTIVE = frozenset({APPLY_HOLD})

EXPIRY_FIELD = "certificate_expiry"

# example.com is RFC 2606-reserved and already this repo's convention for a
# synthetic contact address (see tests/integration/test_frappe_executor.py) —
# guaranteed non-deliverable, never a real supplier's real mailbox. Onboarding
# has no source of a real one today (no CanonicalEvent field, nothing in the
# certificate fixtures carries a contact address), and
# app.executor.frappe.send_supplier_message deliberately fails a Supplier
# with no email_id rather than silently skipping the send — so every
# onboarded supplier needs SOME address or every renewal notice fails
# closed forever, not just until a real one is wired up.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _synthetic_email(supplier_name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", supplier_name or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = _SLUG_RE.sub("-", stripped.casefold()).strip("-")
    return f"{slug or 'supplier'}@example.com"


class Command(BaseModel):
    action: str
    payload: dict = {}


class LifecycleDecision(BaseModel):
    state: str
    cycle: int
    reason: str
    commands: list[Command] = []
    certificate: dict | None = None


def _parse(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except (TypeError, ValueError):
        return None


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _expiry_from_evidence(evidence: dict | None) -> date | None:
    if not isinstance(evidence, dict):
        return None
    for field in evidence.get("fields") or []:
        if isinstance(field, dict) and field.get("name") == EXPIRY_FIELD:
            return _parse(field.get("value"))
    return None


def decide(
    *,
    case_state: dict,
    event: dict,
    evidence: dict | None,
    timing: LifecycleTiming,
) -> LifecycleDecision:
    """Return the next lifecycle state and the commands it implies."""
    lifecycle = case_state.get("lifecycle")
    if not isinstance(lifecycle, dict):
        lifecycle = {}
    certificate = case_state.get("certificate")
    if not isinstance(certificate, dict):
        certificate = {}
    state = lifecycle.get("state") or ONBOARDING
    cycle = _to_int(lifecycle.get("cycle"))
    supplier = case_state.get("supplier") or event.get("supplier") or ""
    event_type = event.get("event_type") or ""

    def no_op(reason: str) -> LifecycleDecision:
        return LifecycleDecision(state=state, cycle=cycle, reason=reason)

    on = _parse(event.get("effective_date"))
    if on is None:
        return no_op("BAD_EFFECTIVE_DATE")

    expiry = _parse(certificate.get("expiry_date"))
    evidence_version = _to_int(certificate.get("evidence_version"))

    if event_type == "new_supplier_packet":
        if state != ONBOARDING:
            return no_op("ALREADY_ONBOARDED")
        commands = [Command(action=CREATE_SUPPLIER,
                            payload={"supplier_name": supplier, "country": "Colombia",
                                     "email_id": _synthetic_email(supplier)})]
        new_expiry = _expiry_from_evidence(evidence)
        if new_expiry is None:
            # Onboarded, but no certificate yet — the renewal clock cannot
            # start until one arrives.
            return LifecycleDecision(state=ONBOARDING, cycle=1,
                                     reason="AWAITING_EVIDENCE", commands=commands)
        commands.append(Command(action=ATTACH_EVIDENCE,
                                payload={"supplier_name": supplier, "cycle": 1}))
        return LifecycleDecision(
            state=ACTIVE,
            cycle=1,
            reason="ONBOARDED",
            commands=commands,
            certificate={"expiry_date": new_expiry.isoformat(), "evidence_version": 1,
                         "document_checksum": (evidence or {}).get("document_checksum"),
                         "extracted_at": on.isoformat()},
        )

    if event_type == "renewal_due":
        if state == RENEWAL_REQUESTED:
            return no_op("ALREADY_REQUESTED")
        if state != ACTIVE:
            return no_op("NOT_APPLICABLE")
        if expiry is None:
            return no_op("NO_CERTIFICATE")
        if on < expiry - timedelta(days=timing.renewal_window_days):
            return no_op("NOT_DUE")
        return LifecycleDecision(
            state=RENEWAL_REQUESTED,
            cycle=cycle,
            reason="RENEWAL_REQUESTED",
            commands=[Command(action=REQUEST_RENEWAL,
                              payload={"supplier_name": supplier,
                                       "expiry_date": expiry.isoformat()})],
        )

    if event_type == "evidence_overdue":
        if state == HELD:
            return no_op("ALREADY_HELD")
        if state != RENEWAL_REQUESTED:
            # A certificate arrived and moved the case on; a late overdue
            # event must not hold a supplier who already renewed.
            return no_op("SUPERSEDED")
        if expiry is None:
            return no_op("NO_CERTIFICATE")
        if on <= expiry + timedelta(days=timing.overdue_grace_days):
            return no_op("NOT_DUE")
        return LifecycleDecision(
            state=HELD,
            cycle=cycle,
            reason="HELD_OVERDUE",
            commands=[Command(action=APPLY_HOLD,
                              payload={"supplier_name": supplier,
                                       "hold_type": "All"})],
        )

    if event_type == "certificate_received":
        new_expiry = _expiry_from_evidence(evidence)
        if new_expiry is None:
            return no_op("NO_EXPIRY_FOUND")
        if expiry is not None and new_expiry <= expiry:
            return no_op("STALE_DOCUMENT")
        next_cycle = cycle + 1
        commands = [Command(action=ATTACH_EVIDENCE,
                            payload={"supplier_name": supplier, "cycle": next_cycle})]
        if state == HELD:
            commands.append(Command(action=CLEAR_HOLD,
                                    payload={"supplier_name": supplier}))
        return LifecycleDecision(
            state=ACTIVE,
            cycle=next_cycle,
            reason="RENEWED",
            commands=commands,
            certificate={"expiry_date": new_expiry.isoformat(),
                         "evidence_version": evidence_version + 1,
                         "document_checksum": (evidence or {}).get("document_checksum"),
                         "extracted_at": on.isoformat()},
        )

    return no_op("UNKNOWN_EVENT_TYPE")
