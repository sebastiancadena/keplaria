"""Unit tests for the deterministic policy/risk module.

assess() is a total function: it never raises, and every failure path
produces a verdict with a reason code. These tests pin the three bands, the
fail-closed paths, and the determinism the replay contract depends on.
"""

from __future__ import annotations

import json

import pytest

from app.risk import (
    BLOCKED,
    CLEAR,
    REVIEW,
    PolicyLoadError,
    assess,
    assess_case,
    load_policy,
    reset_policy_cache,
)

CASE = {"case_id": "CASE-1", "event_type": "new_supplier_packet", "supplier": "Acme"}


def _screening(*, reachable=True, candidates=None, flagged=None) -> dict:
    return {
        "endpoint": "http://10.10.0.2:8000",
        "supplier": "Acme",
        "reachable": reachable,
        "candidates": candidates or [],
        "flagged": flagged or [],
        "error": None,
    }


@pytest.fixture
def policy():
    reset_policy_cache()
    return load_policy()


def test_shipped_fixture_parses_and_registers_every_kind(policy):
    assert policy.policy_id == "supplier_risk"
    assert policy.policy_version == 1
    assert policy.thresholds.review <= policy.thresholds.block
    assert {f.id for f in policy.factors} == {
        "SANCTIONS_MATCH",
        "SUBTHRESHOLD_CANDIDATE",
        "SCREENING_UNAVAILABLE",
    }


def test_clean_supplier_is_clear(policy):
    screening = _screening(candidates=[{"id": "syn-co-100", "score": 0.11, "match": False}])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == CLEAR
    assert verdict.score == 0.0
    assert verdict.factors_fired == []


def test_sanctions_match_blocks(policy):
    screening = _screening(
        candidates=[{"id": "syn-co-001", "score": 1.0, "match": True}],
        flagged=["syn-co-001"],
    )

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    fired = {f.id for f in verdict.factors_fired}
    assert "SANCTIONS_MATCH" in fired


def test_subthreshold_candidate_alone_lands_in_review(policy):
    """The decoy case: near-match, no confirmed hit. A human must look."""
    screening = _screening(candidates=[{"id": "syn-co-008", "score": 0.526, "match": False}])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == REVIEW
    assert [f.id for f in verdict.factors_fired] == ["SUBTHRESHOLD_CANDIDATE"]
    assert verdict.factors_fired[0].value == "syn-co-008 @ 0.526"


def test_unreachable_screening_is_review_not_clear(policy):
    """Fail-closed: an empty `flagged` from a dead service must not read clear."""
    screening = _screening(reachable=False)

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == REVIEW
    assert [f.id for f in verdict.factors_fired] == ["SCREENING_UNAVAILABLE"]


def test_absent_screening_is_clear_not_unreachable(policy):
    """The `skip` branch never screened; that is not the same as a failure."""
    verdict = assess(policy, screening=None, case=CASE)

    assert verdict.band == CLEAR
    assert verdict.factors_fired == []


def test_malformed_screening_blocks(policy):
    verdict = assess(policy, screening={"reachable": "yes-ish"}, case=CASE)

    assert verdict.band == BLOCKED
    assert "SCREENING_MALFORMED" in verdict.reasons


def test_assess_is_deterministic(policy):
    screening = _screening(
        candidates=[
            {"id": "syn-co-001", "score": 1.0, "match": True},
            {"id": "syn-co-008", "score": 0.526, "match": False},
        ],
        flagged=["syn-co-001"],
    )

    first = assess(policy, screening=screening, case=CASE)
    second = assess(policy, screening=screening, case=CASE)

    assert first.model_dump() == second.model_dump()
    assert first.score == pytest.approx(0.95)


def test_missing_fixture_blocks_with_policy_unavailable(tmp_path):
    reset_policy_cache()

    verdict = assess_case(screening=None, case=CASE, path=tmp_path / "nope.json")

    assert verdict.band == BLOCKED
    assert "POLICY_UNAVAILABLE" in verdict.reasons
    reset_policy_cache()


def test_unregistered_condition_kind_is_rejected_at_load(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "policy_id": "p",
                "policy_version": 1,
                "thresholds": {"review": 0.2, "block": 0.6},
                "factors": [{"id": "X", "weight": 0.5, "when": {"kind": "vibes"}}],
            }
        )
    )

    with pytest.raises(PolicyLoadError, match="vibes"):
        load_policy(bad)


def test_thresholds_out_of_order_are_rejected_at_load(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "policy_id": "p",
                "policy_version": 1,
                "thresholds": {"review": 0.9, "block": 0.6},
                "factors": [],
            }
        )
    )

    with pytest.raises(PolicyLoadError, match="threshold"):
        load_policy(bad)


def test_non_dict_screening_blocks(policy):
    """Guard against passing a string or list instead of a dict."""
    for bad_screening in ["not a dict", ["list"], 123]:
        verdict = assess(policy, screening=bad_screening, case=CASE)

        assert verdict.band == BLOCKED
        assert "SCREENING_MALFORMED" in verdict.reasons


def test_flagged_candidate_with_no_score_key_renders_safely(policy):
    """Regression: flagged candidate missing score key should not raise TypeError.

    When a candidate is in the flagged list but has no 'score' key, the value
    description should still render safely with a default of 0.0.
    """
    screening = _screening(
        candidates=[{"id": "syn-co-001", "match": True}],
        flagged=["syn-co-001"],
    )

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    fired = {f.id for f in verdict.factors_fired}
    assert "SANCTIONS_MATCH" in fired
    assert verdict.factors_fired[0].value == "syn-co-001 @ 0.000"


def test_non_dict_candidate_blocks(policy):
    """Regression: a candidate that is not a dict (e.g. a bare string) must
    not raise AttributeError from `.get()` inside the condition kinds — it
    must be caught by _is_malformed and routed to SCREENING_MALFORMED."""
    screening = _screening(candidates=["oops"])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    assert "SCREENING_MALFORMED" in verdict.reasons


def test_stringified_score_blocks(policy):
    """Regression: yente (or a proxy in front of it) returning a stringified
    score must not raise TypeError from `'0.9' >= 0.5` inside the condition
    kinds — it must be caught by _is_malformed and routed to
    SCREENING_MALFORMED. Reachable from production: screen_supplier copies
    `r["score"]` verbatim out of yente's JSON response."""
    screening = _screening(candidates=[{"id": "x", "score": "0.9"}])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    assert "SCREENING_MALFORMED" in verdict.reasons


def test_boolean_score_blocks(policy):
    """bool is a subclass of int in Python; a screening score of True/False
    is never legitimate and must be rejected rather than silently coerced to
    1.0/0.0 by the >= comparisons in the condition kinds."""
    screening = _screening(candidates=[{"id": "x", "score": True}])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    assert "SCREENING_MALFORMED" in verdict.reasons


def test_unhashable_list_id_blocks(policy):
    """Regression: _screening_match builds `{c.get('id'): ...}` — a dict
    comprehension KEYED on id. A list id raises `TypeError: unhashable type:
    'list'` there, before assess() ever gets to decide a band. Reachable
    from production the same way the score fix is: screen_supplier copies
    `r["id"]` verbatim out of yente's JSON with no inner validation."""
    screening = _screening(candidates=[{"id": ["x"], "score": 0.9}], flagged=["y"])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    assert "SCREENING_MALFORMED" in verdict.reasons


def test_unhashable_dict_id_blocks(policy):
    """Same defect class as test_unhashable_list_id_blocks, dict instead of
    list — both are unhashable and raise TypeError from the same dict-key
    comprehension in _screening_match."""
    screening = _screening(candidates=[{"id": {"a": 1}, "score": 0.9}], flagged=["y"])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    assert "SCREENING_MALFORMED" in verdict.reasons


def test_non_string_flagged_entry_blocks(policy):
    """A `flagged` list must contain only candidate ID strings."""
    screening = _screening(
        candidates=[{"id": "x", "score": 0.9, "match": True}], flagged=[123]
    )

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == BLOCKED
    assert "SCREENING_MALFORMED" in verdict.reasons


def test_candidate_above_with_no_score_key_renders_safely(tmp_path):
    """Regression: screening_candidate_above with floor 0.0 and no-score candidate.

    When screening_candidate_above has floor=0.0, a candidate with no 'score' key
    will pass the filter (0.0 >= 0.0) but then the value description must still
    render safely with a default of 0.0, not raise TypeError.
    """
    reset_policy_cache()
    policy_file = tmp_path / "test_policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "policy_id": "test_policy",
                "policy_version": 1,
                "thresholds": {"review": 0.2, "block": 0.6},
                "factors": [
                    {
                        "id": "CANDIDATE_AT_ZERO",
                        "weight": 0.25,
                        "when": {"kind": "screening_candidate_above", "score": 0.0},
                    }
                ],
            }
        )
    )

    policy = load_policy(policy_file)
    screening = _screening(candidates=[{"id": "syn-co-008", "match": False}])

    verdict = assess(policy, screening=screening, case=CASE)

    assert verdict.band == REVIEW
    fired = [f.id for f in verdict.factors_fired]
    assert "CANDIDATE_AT_ZERO" in fired
    assert verdict.factors_fired[0].value == "syn-co-008 @ 0.000"
    reset_policy_cache()
