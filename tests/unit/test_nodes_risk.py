"""Unit tests for the risk gate node and the review terminal.

assess_risk must route on the band and never on model output; park_case must
be a true terminal that claims no command. Together these close the defect
this branch exists to fix at the graph level: a flagged supplier can no
longer reach queue_supplier.
"""

from __future__ import annotations

import app.nodes as nodes_module
from app.nodes import assess_risk, park_case, queue_supplier
from app.state.commands import PENDING, get_command
from app.state.firestore import CASES


class _StubContext:
    """assess_risk and park_case only read ctx.state — a dict wrapper is enough."""

    def __init__(self, state: dict):
        self.state = state


def _case(case_id: str) -> dict:
    return {"case_id": case_id, "event_type": "new_supplier_packet", "supplier": "Acme"}


def _screening(*, reachable=True, candidates=None, flagged=None) -> dict:
    return {
        "endpoint": "http://10.10.0.2:8000",
        "supplier": "Acme",
        "reachable": reachable,
        "candidates": candidates or [],
        "flagged": flagged or [],
        "error": None,
    }


def test_clean_screening_routes_clear(case_id):
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening(candidates=[{"id": "x", "score": 0.1, "match": False}]),
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "clear"
    assert result.output["band"] == "clear"


def test_sanctions_match_routes_blocked(case_id):
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening(
                candidates=[{"id": "syn-co-001", "score": 1.0, "match": True}],
                flagged=["syn-co-001"],
            ),
        }
    )

    result = assess_risk(None, ctx)

    assert result.actions.route == "blocked"
    assert "SANCTIONS_MATCH" in [f["id"] for f in result.output["factors_fired"]]


def test_unreachable_screening_routes_review(case_id):
    ctx = _StubContext({"case": _case(case_id), "screening": _screening(reachable=False)})

    result = assess_risk(None, ctx)

    assert result.actions.route == "review"


def test_absent_screening_routes_clear(case_id):
    """The skip branch still passes the gate, and still gets a verdict."""
    ctx = _StubContext({"case": _case(case_id)})

    result = assess_risk(None, ctx)

    assert result.actions.route == "clear"
    assert result.output["policy_version"] == 1


# The verdict is published to graph state via `Event(state={"policy": ...})`,
# the same mechanism apply_route and screen_supplier already use. It is
# exercised end-to-end by the graph, not asserted here: park_case reading
# ctx.state["policy"] is covered by test_park_case_persists_phase_and_verdict.


def test_park_case_claims_no_command(db, case_id):
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening(candidates=[{"id": "syn-co-008", "score": 0.526, "match": False}]),
            "policy": {"policy_id": "supplier_risk", "policy_version": 1, "score": 0.25,
                       "band": "review", "factors_fired": [], "reasons": []},
        }
    )

    result = park_case(None, ctx)

    assert result.output["status"] == "awaiting_approval"
    assert get_command(db, case_id, "create_supplier", 1) is None


def test_park_case_persists_phase_and_verdict(db, case_id):
    verdict = {"policy_id": "supplier_risk", "policy_version": 1, "score": 0.25,
               "band": "review", "factors_fired": [], "reasons": []}
    ctx = _StubContext({"case": _case(case_id), "screening": _screening(), "policy": verdict})

    park_case(None, ctx)

    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["phase"] == "awaiting_approval"
    assert stored["policy"]["band"] == "review"


def test_a_clock_event_does_not_rescore_a_blocked_case():
    ctx = _StubContext({
        "case": {"case_id": "C1", "event_type": "evidence_overdue"},
        "case_state": {"policy": {"band": "blocked", "score": 0.7,
                                  "policy_id": "supplier_risk", "policy_version": 1,
                                  "reasons": ["SANCTIONS_MATCH"]}},
    })

    event = assess_risk(None, ctx)

    assert event.actions.route == "blocked", (
        "the clock advancing must never launder a blocked supplier into clear"
    )
    assert event.actions.state_delta["policy"]["band"] == "blocked"


def test_a_clock_event_on_a_case_with_no_stored_verdict_is_blocked():
    ctx = _StubContext({"case": {"case_id": "C1", "event_type": "renewal_due"},
                "case_state": {}})

    event = assess_risk(None, ctx)

    assert event.actions.route == "blocked"
    assert event.actions.state_delta["policy"]["reasons"] == ["NO_STORED_VERDICT"]


def test_a_clock_event_carries_a_clear_verdict_forward():
    ctx = _StubContext({
        "case": {"case_id": "C1", "event_type": "renewal_due"},
        "case_state": {"policy": {"band": "clear", "score": 0.0,
                                  "policy_id": "supplier_risk", "policy_version": 1}},
    })

    event = assess_risk(None, ctx)

    assert event.actions.route == "clear"


def test_a_fresh_screening_still_scores_normally():
    ctx = _StubContext({
        "case": {"case_id": "C1", "event_type": "new_supplier_packet"},
        "case_state": {},
        "screening": {"reachable": True, "candidates": [], "flagged": []},
    })

    event = assess_risk(None, ctx)

    assert event.actions.route == "clear"
    assert event.actions.state_delta["policy"]["score"] == 0.0


def test_queue_supplier_persists_a_clear_verdict_alongside_the_claim(db, case_id, monkeypatch):
    """This is the branch's central invariant, made hermetic: no path reaches
    the command queue without a persisted `clear` verdict. app.executor.runner
    re-reads cases/{case_id}.policy before draining a command and refuses
    anything that isn't `clear` — if queue_supplier's `policy` argument to
    _record_outcome were ever dropped, every drain would silently refuse and
    the default suite would still be green. This pins the persisted verdict
    directly, rather than deferring it to a live-marked graph test."""
    monkeypatch.setattr(nodes_module, "get_client", lambda: db)

    verdict = {
        "policy_id": "supplier_risk",
        "policy_version": 1,
        "score": 0.0,
        "band": "clear",
        "factors_fired": [],
        "reasons": [],
    }
    ctx = _StubContext(
        {
            "case": _case(case_id),
            "screening": _screening(candidates=[{"id": "x", "score": 0.1, "match": False}]),
            "policy": verdict,
        }
    )

    result = queue_supplier(None, ctx)

    assert result.output["status"] == "command_queued"

    command = get_command(db, case_id, "create_supplier", 1)
    assert command is not None
    assert command["status"] == PENDING

    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["policy"]["band"] == "clear"
