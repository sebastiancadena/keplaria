"""The judge run's scoreboard, computed from the ledger rather than asserted.

The project's utility claim must be measured, never asserted. This module is
the measuring instrument: given the case documents, the command ledger, and
the event timeline a run actually produced, it returns the numbers that
claim interpolates.

It reads state, it does not create it. Anything it cannot derive from the
run comes back `None` rather than as a default, so a missing input reads as
missing on the scoreboard instead of quietly becoming a zero.
"""

from __future__ import annotations

from datetime import date

DONE = "done"

# The lifecycle events that open and close a restriction. app.lifecycle turns
# an overdue certificate into APPLY_HOLD and an accepted renewal into
# CLEAR_HOLD; these are the event types whose effective dates bound the window.
HOLD_OPENED_BY = "evidence_overdue"
HOLD_CLOSED_BY = "certificate_received"


def _dates(timeline: list[dict]) -> list[date]:
    return sorted(
        date.fromisoformat(entry["effective_date"])
        for entry in timeline
        if entry.get("effective_date")
    )


def _first_date(timeline: list[dict], event_type: str) -> date | None:
    for entry in timeline:
        if entry.get("event_type") == event_type and entry.get("effective_date"):
            return date.fromisoformat(entry["effective_date"])
    return None


def _simulated_business_days(timeline: list[dict]) -> int | None:
    """Span of the simulated lifecycle clock, which is NOT wall-clock time.

    Reported separately from automation seconds and always labelled, because
    conflating a 380-day simulated lifecycle with a 75-second run would be
    the single most misleading number this scoreboard could produce.
    """
    days = _dates(timeline)
    return (days[-1] - days[0]).days if len(days) >= 2 else None


def _enforced_hold_days(commands: list[dict], timeline: list[dict]) -> int | None:
    """Days a non-compliant supplier was actually restricted from purchasing.

    Requires BOTH the timeline dates and the executed commands. The dates on
    their own describe an intention; only a done `apply_hold` and a done
    `clear_hold` mean a real restriction existed in the ERP and was lifted.
    A claimed hold that never drained restricted nobody, and saying otherwise
    would be exactly the kind of unearned claim this module exists to prevent.
    """
    executed = {c.get("action") for c in commands if c.get("status") == DONE}
    if not {"apply_hold", "clear_hold"} <= executed:
        return None
    opened = _first_date(timeline, HOLD_OPENED_BY)
    closed = _first_date(timeline, HOLD_CLOSED_BY)
    if opened is None or closed is None:
        return None
    return (closed - opened).days


def _retried(command: dict) -> bool:
    """Did this command fail at least once before it succeeded?

    `execution_attempts` is incremented ONLY by app.state.commands.record_failure,
    which means the field is ABSENT on a command that succeeded first try — not
    zero. So the signature of a failed-then-succeeded command is a present value
    of at least 1, and `> 1` is the wrong test: it reads the single-failure case,
    which is the only one this system has ever actually produced, as a clean run.
    That misreading briefly made the retry contract look like it had a hole in it.

    Not to be confused with `attempts`, which claim_command increments graph-side
    on every claim and which grows for reasons that have nothing to do with the
    ERP call.
    """
    return command.get("status") == DONE and int(command.get("execution_attempts") or 0) >= 1


def _write_key(command: dict) -> tuple:
    """What "the same write" means: one action, one subject, one cycle.

    The cycle is part of the key because the lifecycle repeats by design — a
    renewed supplier attaches a second certificate in cycle 2, and that is
    correct behaviour, not a double write. Dropping the cycle from this key
    would report every renewal as a duplicate.
    """
    payload = command.get("payload") or {}
    subject = command.get("case_id") or payload.get("supplier_name")
    return (subject, command.get("action"), command.get("cycle"))


def _duplicate_writes(commands: list[dict]) -> int:
    """Done commands that share a write key — i.e. the same write, twice.

    Deterministic command ids are supposed to make this structurally
    impossible. Counting it anyway is the difference between measuring the
    exactly-once guarantee and assuming it: a metric that cannot come back
    non-zero is not evidence of anything.
    """
    seen: dict[tuple, int] = {}
    for command in commands:
        if command.get("status") != DONE:
            continue
        key = _write_key(command)
        seen[key] = seen.get(key, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


# What each action writes into the ERP that a human would otherwise have typed
# or selected in the native UI. Deliberately NOT the raw REST payload: the
# Communication create sends nine keys, but `communication_type`,
# `sent_or_received`, `reference_doctype` and friends are transport scaffolding
# the UI's compose form fills in implicitly, and counting them would inflate the
# claim. Every entry below is a field a person would actually have to enter.
# Cross-check against app.executor.frappe before changing a count.
ERP_UI_FIELDS = {
    # create_supplier_if_absent: supplier_group and supplier_type are always
    # sent; email_id is opt-in and omitted from the payload entirely when falsy.
    "create_supplier": ("supplier_name", "supplier_group", "supplier_type", "country"),
    # set_supplier_hold writes on_hold/hold_type/release_date; release_date is
    # always None, which is not a typed value.
    "apply_hold": ("on_hold", "hold_type"),
    # clear_supplier_hold blanks hold_type as a side effect of clearing on_hold.
    "clear_hold": ("on_hold",),
    # send_supplier_message: the recipient is resolved from the Supplier record
    # rather than typed, which is itself the point — it is still a value the
    # human did not have to look up.
    "request_renewal": ("recipients", "subject", "content"),
    # attach_evidence: the file and its deterministic name.
    "attach_evidence": ("file_name", "content"),
}

# Payload keys that add a field beyond the action's fixed set when present.
_OPTIONAL_FIELDS = {"create_supplier": ("email_id",)}


def _fields_entered(command: dict) -> int:
    """ERP fields this command wrote without a human transcribing them.

    Zero unless the command actually executed: a parked or refused command
    entered nothing, and counting intent rather than effect is precisely the
    kind of claim this scoreboard exists to avoid making.
    """
    if command.get("status") != DONE:
        return 0
    action = command.get("action")
    fields = ERP_UI_FIELDS.get(action)
    if fields is None:
        return 0
    payload = command.get("payload") or {}
    optional = sum(
        1 for key in _OPTIONAL_FIELDS.get(action, ()) if payload.get(key)
    )
    return len(fields) + optional


def _manual_steps_eliminated(baseline: dict | None, interventions: int) -> int | None:
    """Baseline manual steps the system removed, minus the ones it escalated.

    `None` without a baseline, deliberately: 0 would read as "nothing was
    eliminated" and any invented figure would be exactly the fabricated
    validation this project forbids. The baseline this
    consumes is author-timed rather than practitioner-reviewed, and the
    `baseline_validation` string travels with the number so the qualifier
    cannot be separated from the claim downstream.
    """
    if not baseline:
        return None
    steps = baseline.get("manual_steps")
    if steps is None:
        return None
    return int(steps) - interventions


def scoreboard(
    cases: list[dict],
    commands: list[dict],
    timeline: list[dict],
    baseline: dict | None = None,
) -> dict:
    """Every scored metric for one judge run."""
    interventions = sum(
        1 for case in cases if (case.get("approval") or {}).get("decision")
    )
    return {
        "policy_required_interventions": interventions,
        "workflow_steps_completed": sum(
            1 for entry in timeline if entry.get("ok", True)
        ),
        "workflow_steps_total": len(timeline),
        "manual_steps_eliminated": _manual_steps_eliminated(baseline, interventions),
        "baseline_validation": (baseline or {}).get("validation"),
        "commands_retried_then_succeeded": sum(
            1 for command in commands if _retried(command)
        ),
        "duplicate_writes_after_retry": _duplicate_writes(commands),
        "fields_without_rekeying": sum(_fields_entered(c) for c in commands),
        "simulated_business_days": _simulated_business_days(timeline),
        "enforced_hold_days": _enforced_hold_days(commands, timeline),
    }
