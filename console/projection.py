"""The public view model, built by naming every field it emits.

This is an allowlist and must stay one. A blacklist would be correct today and
wrong the first time someone adds a field to the case document, because the
default for a new field would be "published". The same argument, and the same
shape, as the write-boundary projection in app/nodes.py.

Withheld deliberately: the screening endpoint (internal topology), the approval
actor (a real account email), injection finding offsets, command payloads (they
carry case detail), and the compliance block (its stored shape has not been
surveyed field by field, and an allowlist may not emit what it has not read).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.executor.runner import effective_band


def _routing(routing: dict | None) -> dict | None:
    if not routing:
        return None
    return {
        "proposed": list(routing.get("proposed") or []),
        "route": list(routing.get("route") or []),
        "dropped": list(routing.get("dropped") or []),
        "added": list(routing.get("added") or []),
        "reason": routing.get("reason"),
        "refused": routing.get("refused"),
        "department": routing.get("department"),
        "department_source": routing.get("department_source"),
        "evidence_skipped_no_document": bool(
            routing.get("evidence_skipped_no_document")
        ),
        "evidence_skipped_tainted_document": bool(
            routing.get("evidence_skipped_tainted_document")
        ),
    }


#: Command states that mean the ERP was actually written.
_DONE = "done"
#: ...and the states that mean it deliberately was not.
_UNRUN = ("held", "pending", "refused_by_policy")

#: The five stops a reader is shown, in lifecycle order. "released" is not a
#: stored state -- it is `active` again after a hold was cleared, and showing
#: it as plain "Active" would erase the story the strip exists to tell.
_LIFECYCLE_STEPS = (
    ("onboarding", "Onboarded"),
    ("active", "Active"),
    ("renewal_requested", "Renewal requested"),
    ("held", "Purchasing held"),
    ("released", "Hold released"),
)


def _lifecycle(case: dict, commands: list[dict]) -> dict:
    """Where this case sits in the supplier lifecycle, for a cold reader.

    Derived from the lifecycle block the graph persists, plus one command
    fact (a done clear_hold marks a released case). A state outside the
    known five -- including quarantined -- highlights nothing rather than
    guessing.
    """
    state = (case.get("lifecycle") or {}).get("state") or "onboarding"
    released = state == "active" and any(
        c.get("action") == "clear_hold" and c.get("status") == _DONE
        for c in commands
    )
    step = "released" if released else state
    known = {key for key, _ in _LIFECYCLE_STEPS}
    current = step if step in known else None
    return {
        "state": state,
        "step": current,
        "steps": [
            {"key": key, "label": label, "current": key == current}
            for key, label in _LIFECYCLE_STEPS
        ],
        "quarantined": state == "quarantined",
    }


def _status(case: dict, commands: list[dict], effective: str | None,
            gate: str | None, approval_id: str | None) -> dict:
    """Derive what a reader should be told, from what actually happened.

    NOT from `phase`. `phase` is the graph's own bookkeeping and
    `commit_approval` never touches it -- only `app.nodes.park_case` writes
    it -- so an approved, executed case still carries `awaiting_approval`.
    Rendering that as a status tells a viewer the case is waiting seconds
    after its ERP rows appeared, which during the video's live segment
    contradicts the narration out loud.

    The states are ordered by what a reader most needs to know first: a
    blocked case will never write, a parked one has not written YET, and the
    difference between those two is the entire point of the review band.
    """
    written = sum(1 for c in commands if c.get("status") == _DONE)
    held = sum(1 for c in commands if c.get("status") in _UNRUN)
    total = len(commands)

    if gate == "blocked":
        return {"state": "BLOCKED", "erp_writes": written, "held": held,
                "summary": "Blocked by policy. Nothing will be written to "
                           "the ERP."}
    if approval_id and written and not held:
        return {"state": "EXECUTED", "erp_writes": written, "held": held,
                "summary": f"Complete. {_plural(written, 'command')} "
                           f"written to the ERP."}
    if approval_id:
        return {"state": "APPROVED", "erp_writes": written, "held": held,
                "summary": "Released by a human at Ground Control. The "
                           "gate's verdict stays review; the release changes "
                           "only the effective band."}
    if case.get("phase") == "awaiting_approval":
        return {"state": "PARKED", "erp_writes": written, "held": held,
                "summary": "Stopped, pending a human. "
                           f"{_plural(held, 'command')} held — nothing has "
                           "been written to the ERP."}
    if written and not held:
        return {"state": "EXECUTED", "erp_writes": written, "held": held,
                "summary": f"Complete. {_plural(written, 'command')} "
                           f"written to the ERP."}
    return {"state": "PROCESSING", "erp_writes": written, "held": held,
            "summary": f"Payload received. {_plural(total, 'command')} "
                       "queued so far; nothing has been written to the ERP."}


def _plural(n: int, noun: str) -> str:
    """`candidate(s)` never ships."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _candidates(screening: dict) -> list[dict]:
    """Screening rows, best match first.

    `caption` is yente's human-readable name and was already being captured
    in app/nodes.py; the allowlist simply never emitted it, so the table read
    `syn-co-008` where it could have read the supplier's near-twin. `topics`
    comes along for the same reason -- it is what makes a row a sanctions
    candidate rather than a coincidence.
    """
    rows = [
        {
            "id": c.get("id"),
            "caption": c.get("caption"),
            "score": c.get("score"),
            "match": c.get("match"),
            "topics": list(c.get("topics") or []),
        }
        for c in (screening.get("candidates") or [])
        if isinstance(c, dict)
    ]
    return sorted(rows, key=lambda c: c.get("score") or 0, reverse=True)


def _cited_candidate_ids(policy: dict, candidates: list[dict]) -> list[str]:
    """Which screening candidates a fired risk factor actually names.

    The factor records its candidate inside a human-readable value
    ("syn-co-008 @ 0.672"), so this reads the ids back out of it rather than
    guessing. The tempting shortcut -- mark the highest-scoring row -- is a
    heuristic that agrees with the truth today and lies the first time a
    factor cites anything else.
    """
    values = " ".join(
        str(f.get("value", "")) for f in (policy.get("factors_fired") or [])
        if isinstance(f, dict)
    )
    # `id` is producer-supplied and is not guaranteed to be a string: a
    # malformed record must leave the row uncited, never take the page down.
    return [
        c["id"] for c in candidates
        if isinstance(c.get("id"), str) and c["id"] and c["id"] in values
    ]


def public_case(case: dict, commands: Iterable[dict] = ()) -> dict:
    """Project a raw case document to the public view model."""
    effective, gate, approval_id = effective_band(case)
    # Materialised once: `commands` is an Iterable and the status derivation
    # and the command list both consume it. A generator would leave the
    # second reader with nothing, and the page would silently show no
    # commands on exactly the cases that have them.
    commands = list(commands)
    screening = case.get("screening") or {}
    injection = case.get("injection") or {}
    policy = case.get("policy") or {}
    approval = case.get("approval") or {}

    return {
        "case_id": case.get("case_id"),
        "status": _status(case, commands, effective, gate, approval_id),
        "lifecycle": _lifecycle(case, commands),
        "cited_candidate_ids": _cited_candidate_ids(policy, _candidates(screening)),
        "case_version": case.get("case_version"),
        "phase": case.get("phase"),
        "supplier": case.get("supplier"),
        "updated_at": case.get("updated_at"),
        "routing": _routing(case.get("routing")),
        "screening": {
            "reachable": screening.get("reachable"),
            "flagged": list(screening.get("flagged") or []),
            "candidate_count": screening.get("candidate_count"),
            "candidates": _candidates(screening),
        },
        "injection": {
            "tainted": bool(injection.get("tainted")),
            "finding_count": injection.get("finding_count"),
        },
        "policy": {
            "policy_id": policy.get("policy_id"),
            "policy_version": policy.get("policy_version"),
            "score": policy.get("score"),
            "band": policy.get("band"),
            "factors_fired": list(policy.get("factors_fired") or []),
            "reasons": list(policy.get("reasons") or []),
        },
        "gate_band": gate,
        "effective_band": effective,
        "approval": {
            "decision": approval.get("decision"),
            "case_version": approval.get("case_version"),
            "applies": approval_id is not None,
        }
        if approval
        else None,
        "commands": [
            {
                "action": c.get("action"),
                "status": c.get("status"),
                "cycle": c.get("cycle"),
            }
            for c in commands
        ],
        # Persisted by app.nodes._claim_lifecycle_commands directly onto the
        # case document — not the outbox, which a refused command never
        # reaches. Named explicitly rather than folded into "commands" above:
        # commands there are read from the outbox subcollection passed in as
        # `commands`, a wholly different source, and a refusal must stay
        # visually and textually distinct from a queued/executed command.
        "refused_commands": [
            {
                "action": c.get("action"),
                "cycle": c.get("cycle"),
            }
            for c in (case.get("refused_commands") or [])
            if isinstance(c, dict)
        ],
    }
