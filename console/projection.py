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


def public_case(case: dict, commands: Iterable[dict] = ()) -> dict:
    """Project a raw case document to the public view model."""
    effective, gate, approval_id = effective_band(case)
    screening = case.get("screening") or {}
    injection = case.get("injection") or {}
    policy = case.get("policy") or {}
    approval = case.get("approval") or {}

    return {
        "case_id": case.get("case_id"),
        "case_version": case.get("case_version"),
        "phase": case.get("phase"),
        "supplier": case.get("supplier"),
        "updated_at": case.get("updated_at"),
        "routing": _routing(case.get("routing")),
        "screening": {
            "reachable": screening.get("reachable"),
            "flagged": list(screening.get("flagged") or []),
            "candidate_count": screening.get("candidate_count"),
            "candidates": [
                {
                    "id": c.get("id"),
                    "score": c.get("score"),
                    "match": c.get("match"),
                }
                for c in (screening.get("candidates") or [])
                if isinstance(c, dict)
            ],
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
