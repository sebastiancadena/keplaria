"""Unit tests for the risk gate node and the review terminal.

assess_risk must route on the band and never on model output; park_case must
be a true terminal that claims no command. Together these close the defect
this branch exists to fix at the graph level: a flagged supplier can no
longer reach commit_commands.
"""

from __future__ import annotations

import app.nodes as nodes_module
from app.nodes import assess_risk, commit_commands, park_case
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


def test_new_supplier_packet_always_carries_real_screening(case_id):
    """Replaces a prior test of this name that fed assess_risk a
    new_supplier_packet with NO screening and asserted `clear` — that no
    longer matches production and, worse, no longer matches what this gate
    is supposed to do: `new_supplier_packet`'s permitted route is
    {evidence, compliance} on both the with-document and no-document paths
    (see app/policy.py's ALLOWED_ROUTES and app/nodes.py's apply_route), so
    it always reaches screen_supplier and always has a fresh `screening`
    dict by the time it reaches this gate. See
    test_certificate_received_with_no_screening_carries_forward_instead_of_clear
    below for the scenario the old version of this test was actually
    guarding against without saying so — an agentic event reaching this gate
    with no screening of its own — which is now handled correctly."""
    ctx = _StubContext({
        "case": _case(case_id),
        "screening": _screening(candidates=[{"id": "x", "score": 0.1, "match": False}]),
    })

    result = assess_risk(None, ctx)

    assert result.actions.route == "clear"
    assert result.output["policy_version"] == 2


def test_certificate_received_with_no_screening_carries_forward_instead_of_clear():
    """The exact defect this fixes. certificate_received's permitted route
    is {evidence} only, so it never populates `screening` — re-scoring it
    fresh from `screening=None` fires no factors, scores 0.0, and lands
    `clear`, laundering a previously blocked supplier via a mailed-in
    certificate. Carrying forward must trigger on `screening is None`, not
    on "is this a clock event" (certificate_received is not a clock event —
    see CLOCK_EVENTS — so the old event-type check let this exact case
    through)."""
    ctx = _StubContext({
        "case": {"case_id": "C1", "event_type": "certificate_received"},
        "case_state": {"policy": {"band": "blocked", "score": 0.95,
                                  "policy_id": "supplier_risk", "policy_version": 1,
                                  "reasons": ["SANCTIONS_MATCH"]}},
    })

    event = assess_risk(None, ctx)

    assert event.actions.route == "blocked", (
        "a renewal certificate must never launder a blocked supplier into clear"
    )
    assert event.actions.state_delta["policy"]["band"] == "blocked"


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


def test_a_clock_event_carries_a_review_verdict_forward():
    """review is the band nothing else pins: it's the one that diverts a case
    to human sign-off, so a silent downgrade to clear here would remove the
    human from the loop, not merely misprice risk. Reconstruction via
    RiskVerdict(**stored) is band-agnostic today, but that is a fact about
    the current structure — this test is what notices if a later "clarify
    the carry-forward branch into explicit per-band handling" change ever
    drops review through the rescoring path instead."""
    ctx = _StubContext({
        "case": {"case_id": "C1", "event_type": "evidence_overdue"},
        "case_state": {"policy": {"band": "review", "score": 0.4,
                                  "policy_id": "supplier_risk", "policy_version": 1,
                                  "reasons": ["SCREENING_UNAVAILABLE"]}},
    })

    event = assess_risk(None, ctx)

    assert event.actions.route == "review", (
        "a stored review verdict must survive a clock event neither raised "
        "to blocked nor lowered to clear"
    )
    assert event.actions.state_delta["policy"]["band"] == "review"


def test_a_clock_event_with_a_malformed_stored_verdict_fails_closed():
    """band is truthy so this reaches RiskVerdict(**stored) rather than the
    NO_STORED_VERDICT branch; policy_id/policy_version/score are absent, which
    RiskVerdict requires, so construction raises ValidationError and trips
    the except clause under test."""
    ctx = _StubContext({
        "case": {"case_id": "C1", "event_type": "renewal_due"},
        "case_state": {"policy": {"band": "blocked"}},
    })

    event = assess_risk(None, ctx)

    assert event.actions.route == "blocked"
    assert event.actions.state_delta["policy"]["reasons"] == ["STORED_VERDICT_MALFORMED"]


def test_a_fresh_screening_still_scores_normally():
    ctx = _StubContext({
        "case": {"case_id": "C1", "event_type": "new_supplier_packet"},
        "case_state": {},
        "screening": {"reachable": True, "candidates": [], "flagged": []},
    })

    event = assess_risk(None, ctx)

    assert event.actions.route == "clear"
    assert event.actions.state_delta["policy"]["score"] == 0.0


def test_commit_commands_persists_a_clear_verdict_alongside_the_claim(db, case_id, monkeypatch):
    """This is the branch's central invariant, made hermetic: no path reaches
    the write terminal without a persisted `clear` verdict. app.executor.runner
    re-reads cases/{case_id}.policy before draining a command and refuses
    anything that isn't `clear` — if commit_commands' `policy` argument to
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
            "case": {**_case(case_id), "effective_date": "2026-01-01"},
            "case_state": {},
            "screening": _screening(candidates=[{"id": "x", "score": 0.1, "match": False}]),
            "policy": verdict,
        }
    )

    result = commit_commands(None, ctx)

    assert result.output["status"] == "committed"

    command = get_command(db, case_id, "create_supplier", 1)
    assert command is not None
    assert command["status"] == PENDING

    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["policy"]["band"] == "clear"


def test_commit_commands_preserves_stored_routing_and_screening_on_a_clock_event(
    db, case_id, monkeypatch
):
    """A clock event's ctx.state carries no 'routing' and no 'screening' key
    at all — load_case_state's coordinator bypass sends a clock event
    straight to assess_risk, so neither apply_route nor screen_supplier ever
    run for it (see app.agent's clock/agentic split). Before the
    _record_outcome fix, merge=True with an explicit routing: None /
    screening: None nulled the onboarding-time routing decision and
    screening summary on the very first clock tick — confirmed in the
    committed lifecycle harness evidence, which shows 'routing': null read
    back from Firestore after step 1 had written a full routing decision.
    This pins that a clock event through commit_commands leaves both blocks
    exactly as onboarding left them.

    evidence_overdue on an already-HELD case is used because it short-circuits
    to ALREADY_HELD before any expiry-window comparison, so this test needs
    no dependency on the loaded policy's timing thresholds."""
    monkeypatch.setattr(nodes_module, "get_client", lambda: db)

    stored_routing = {
        "proposed": ["evidence", "compliance"],
        "route": ["evidence", "compliance"],
        "dropped": [],
        "reason": "new supplier",
        "refused": None,
        "evidence_skipped_no_document": False,
    }
    stored_screening = {
        "reachable": True,
        "endpoint": "http://10.10.0.2:8000",
        "flagged": [],
        "candidate_count": 0,
        "candidates": [],
    }
    db.collection(CASES).document(case_id).set({
        "case_id": case_id,
        "routing": stored_routing,
        "screening": stored_screening,
        "policy": {"policy_id": "supplier_risk", "policy_version": 1, "score": 0.0,
                   "band": "clear", "factors_fired": [], "reasons": []},
        "lifecycle": {"state": "held", "cycle": 1},
        "certificate": {"expiry_date": "2027-01-01", "evidence_version": 1},
        "supplier": "Andes",
    })
    case_state = db.collection(CASES).document(case_id).get().to_dict()

    ctx = _StubContext({
        "case": {"case_id": case_id, "event_type": "evidence_overdue",
                 "supplier": "Andes", "effective_date": "2026-01-01"},
        "case_state": case_state,
        # No "routing" or "screening" key in ctx.state at all — exactly what
        # a clock event carries.
    })

    event = commit_commands(None, ctx)

    assert event.output["reason"] == "ALREADY_HELD"

    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["routing"] == stored_routing, (
        "a clock event must never null out the onboarding-time routing record"
    )
    assert stored["screening"] == stored_screening, (
        "a clock event must never null out the onboarding-time screening summary"
    )


def test_a_tainted_document_blocks_on_the_fresh_scoring_path(case_id):
    ctx = _StubContext({
        "case": _case(case_id),
        "screening": _screening(candidates=[{"id": "x", "score": 0.1, "match": False}]),
        "document_tainted": True,
    })

    result = assess_risk(None, ctx)

    assert result.actions.route == "blocked"
    assert "DOCUMENT_INJECTION" in [f["id"] for f in result.output["factors_fired"]]


def test_a_tainted_document_blocks_a_carried_forward_clear_verdict(case_id):
    """The path Task 4's factor cannot reach. A certificate_received never
    screens, so assess_risk carries the stored verdict forward and never scores
    at all — without the override, a tainted certificate would inherit the
    supplier's previous `clear` band and commit."""
    ctx = _StubContext({
        "case": _case(case_id),
        "screening": None,
        "case_state": {"policy": {"policy_id": "supplier_risk", "policy_version": 2,
                                  "score": 0.0, "band": "clear",
                                  "factors_fired": [], "reasons": []}},
        "document_tainted": True,
    })

    result = assess_risk(None, ctx)

    assert result.actions.route == "blocked"
    assert "DOCUMENT_INJECTION" in result.output["reasons"]


def test_a_clean_certificate_still_carries_its_verdict_forward(case_id):
    """The regression guard: the override must not disturb the carry-forward
    behaviour that stops time laundering a verdict."""
    ctx = _StubContext({
        "case": _case(case_id),
        "screening": None,
        "case_state": {"policy": {"policy_id": "supplier_risk", "policy_version": 2,
                                  "score": 0.0, "band": "clear",
                                  "factors_fired": [], "reasons": []}},
        "document_tainted": False,
    })

    result = assess_risk(None, ctx)

    assert result.actions.route == "clear"
    assert "DOCUMENT_INJECTION" not in result.output["reasons"]


def test_the_taint_override_only_tightens(case_id):
    """One-directional, like every other guard here. A case already blocked for
    a sanctions match keeps its original reasons rather than being restated as
    an injection case."""
    ctx = _StubContext({
        "case": _case(case_id),
        "screening": _screening(candidates=[{"id": "s", "score": 1.0, "match": True}],
                                flagged=["s"]),
        "document_tainted": True,
    })

    result = assess_risk(None, ctx)

    assert result.actions.route == "blocked"
    assert "SANCTIONS_MATCH" in [f["id"] for f in result.output["factors_fired"]]
