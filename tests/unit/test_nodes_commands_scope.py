"""Department command scope at claim time — the clock path's only check.

Deliberately built on the CLOCK path: on the agentic path a finance event
already refuses at validate_route and never reaches a claim, so an
agentic-path version of the first test would pass with the scope check
deleted — vacuous. The clock path reaches the terminals without ever
passing validate_route; this is precisely the hole the check closes.
"""

from __future__ import annotations

from app.nodes import REFUSED_BY_DEPARTMENT, commit_commands
from app.state.commands import get_command
from app.state.firestore import CASES


class _StubContext:
    def __init__(self, state: dict):
        self.state = state


def _clock_ctx(case_id: str, department: str) -> _StubContext:
    """A renewal_due clock event against an active case whose certificate
    is inside the renewal window (policy fixture: 35 days)."""
    return _StubContext({
        "case": {
            "case_id": case_id,
            "event_type": "renewal_due",
            "supplier": "Andes Foods",
            "effective_date": "2026-08-20",
            "department": department,
        },
        "case_state": {
            "supplier": "Andes Foods",
            "lifecycle": {"state": "active", "cycle": 1},
            "certificate": {"expiry_date": "2026-09-01", "evidence_version": 1},
        },
    })


def test_a_clock_command_outside_the_department_scope_is_refused_at_claim(
    db, case_id
):
    """finance permits no commands: the renewal the lifecycle names is
    refused and recorded, and no outbox row exists for the executor to
    ever see. Refused-and-recorded, per the boundary rule — nothing here
    claims the producer was prevented from publishing the event."""
    result = commit_commands(None, _clock_ctx(case_id, "finance"))

    commands = result.output["commands"]
    assert [c["action"] for c in commands] == ["request_renewal"]
    assert commands[0]["status"] == REFUSED_BY_DEPARTMENT
    assert get_command(db, case_id, "request_renewal", 1) is None


def test_a_fully_refused_case_persists_as_no_action_not_committed(db, case_id):
    """decision.commands (decide()'s proposal) is non-empty here — the
    renewal is genuinely due — but every one of those commands is refused
    at claim time, so the persisted phase must read `no_action`, not
    `committed`: `committed` would be indistinguishable on the case list
    from a real commit the executor simply has not drained yet."""
    result = commit_commands(None, _clock_ctx(case_id, "finance"))

    assert result.output["status"] == "no_action"
    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["phase"] == "no_action"


def test_a_refused_command_is_persisted_on_the_case_document(db, case_id):
    """The claim-time refusal must leave a durable trace even though it
    creates no outbox row: console/store.py's load_case builds the
    console's command list from the outbox subcollection alone, so a
    refusal that lived only in the returned Event would be invisible to
    every console page. `refused_commands` is written onto the case
    document in the same write `_claim_lifecycle_commands` already makes."""
    commit_commands(None, _clock_ctx(case_id, "finance"))

    stored = db.collection(CASES).document(case_id).get().to_dict()
    refused = stored.get("refused_commands")
    assert refused is not None, "the refusal must be persisted, not only returned"
    assert [c["action"] for c in refused] == ["request_renewal"]
    assert refused[0]["status"] == REFUSED_BY_DEPARTMENT


def test_command_scopes_distinguish_procurement_from_compliance(db, case_id):
    """With one permitted_agents list the two departments share agent
    scope; permitted_commands is the ONLY observable runtime difference
    between them, and this test is what goes red if that difference
    silently stops being enforced. create_supplier — a command the system
    actually produces — carries the 'cannot originate supplier writes'
    denial."""
    def onboarding_ctx(department: str) -> _StubContext:
        return _StubContext({
            "case": {
                "case_id": case_id,
                "event_type": "new_supplier_packet",
                "supplier": "Andes Foods",
                "effective_date": "2026-08-20",
                "department": department,
            },
            "case_state": {},
        })

    refused = commit_commands(None, onboarding_ctx("compliance"))
    assert refused.output["commands"][0]["action"] == "create_supplier"
    assert refused.output["commands"][0]["status"] == REFUSED_BY_DEPARTMENT
    assert get_command(db, case_id, "create_supplier", 1) is None

    claimed = commit_commands(None, onboarding_ctx("procurement"))
    assert claimed.output["commands"][0]["action"] == "create_supplier"
    assert claimed.output["commands"][0]["status"] == "queued"
    assert get_command(db, case_id, "create_supplier", 1) is not None
