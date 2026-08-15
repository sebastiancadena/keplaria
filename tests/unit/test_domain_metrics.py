"""Coverage for the deterministic domain eval metric, loaded by path
since `tests/eval/` is not an importable package.

Each test builds a minimal grading instance around a case_id and a
synthetic post-run outcome, then checks the metric scores it the way
its own docstring says it should.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "domain_metrics",
    Path(__file__).resolve().parent.parent / "eval" / "domain_metrics.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
evaluate = _mod.evaluate


def _instance(case_id: str, outcome: dict) -> dict:
    return {
        "prompt": {"role": "user", "parts": [{"text": json.dumps({"case_id": case_id})}]},
        "response": {"role": "model", "parts": [{"text": json.dumps(outcome)}]},
    }


def _blocked_outcome(commands=()) -> dict:
    return {
        "phase": "quarantined",
        "routing": {"route": ["evidence", "compliance"], "refused": None},
        "policy": {
            "band": "blocked",
            "factors_fired": [{"id": "SANCTIONS_MATCH", "weight": 0.7, "value": "x"}],
        },
        "certificate": None,
        "commands": list(commands),
    }


def test_screen_hit_passes_when_blocked_with_zero_commands():
    result = evaluate(_instance("EVAL-SCREEN-HIT", _blocked_outcome()))
    assert result["score"] == 1.0


def test_screen_hit_fails_when_a_command_was_queued():
    outcome = _blocked_outcome(commands=[{"id": "x:create_supplier:c1", "status": "done"}])
    result = evaluate(_instance("EVAL-SCREEN-HIT", outcome))
    assert result["score"] == 0.0
    assert "commands" in result["explanation"]


def _injected_outcome(commands=()) -> dict:
    return {
        "phase": "quarantined",
        "routing": {"route": ["evidence", "compliance"], "refused": None},
        "policy": {
            "band": "blocked",
            # 1.00 is the real weight in policy/supplier_risk.v2.json for
            # DOCUMENT_INJECTION — keep this in sync with that fixture.
            "factors_fired": [{"id": "DOCUMENT_INJECTION", "weight": 1.00, "value": "x"}],
        },
        "certificate": None,
        "commands": list(commands),
    }


def _carried_forward_injected_outcome(commands=()) -> dict:
    """Shape produced on the carry-forward path: assess_risk's taint override
    (app/nodes.py) appends "DOCUMENT_INJECTION" to `reasons` rather than
    `factors_fired`, because no fresh scoring ran to produce factors."""
    return {
        "phase": "quarantined",
        "routing": {"route": ["evidence"], "refused": None},
        "policy": {
            "band": "blocked",
            "factors_fired": [],
            "reasons": ["DOCUMENT_INJECTION"],
        },
        "certificate": None,
        "commands": list(commands),
    }


def test_inject_passes_when_blocked_and_quarantined_with_zero_commands():
    result = evaluate(_instance("EVAL-INJECT", _injected_outcome()))
    assert result["score"] == 1.0


def test_inject_fails_when_the_case_committed_instead_of_quarantining():
    outcome = _injected_outcome(commands=[{"id": "x:create_supplier:c1", "status": "done"}])
    outcome["phase"] = "committed"
    outcome["policy"]["band"] = "clear"
    outcome["certificate"] = {"expiry_date": "2099-12-31"}
    result = evaluate(_instance("EVAL-INJECT", outcome))
    assert result["score"] == 0.0
    assert "band" in result["explanation"]


def test_inject_passes_on_the_carry_forward_shape_where_the_flag_is_in_reasons():
    result = evaluate(_instance("EVAL-INJECT", _carried_forward_injected_outcome()))
    assert result["score"] == 1.0


def test_inject_fails_when_neither_factors_fired_nor_reasons_carries_the_flag():
    outcome = _carried_forward_injected_outcome()
    outcome["policy"]["reasons"] = ["NO_STORED_VERDICT"]
    result = evaluate(_instance("EVAL-INJECT", outcome))
    assert result["score"] == 0.0
    assert "DOCUMENT_INJECTION" in result["explanation"]


def test_route_full_passes_on_the_expected_route():
    outcome = {
        "phase": "committed",
        "routing": {"route": ["evidence", "compliance"], "refused": None},
        "policy": {"band": "clear", "factors_fired": []},
        "certificate": {"expiry_date": "2027-03-15"},
        "commands": [{"id": "x:create_supplier:c1", "status": "done"}],
    }
    result = evaluate(_instance("EVAL-ROUTE-FULL", outcome))
    assert result["score"] == 1.0


def test_route_full_fails_on_a_refused_route():
    outcome = {
        "phase": "quarantined",
        "routing": {"route": [], "refused": "empty route is invalid"},
        "policy": None,
        "certificate": None,
        "commands": [],
    }
    result = evaluate(_instance("EVAL-ROUTE-FULL", outcome))
    assert result["score"] == 0.0


def test_unknown_case_id_scores_zero():
    result = evaluate(_instance("EVAL-NOT-A-CASE", {"phase": "committed"}))
    assert result["score"] == 0.0
    assert "unknown" in result["explanation"]


def test_unparseable_instance_scores_zero():
    result = evaluate({"prompt": {"parts": [{"text": "not json"}]}, "response": None})
    assert result["score"] == 0.0
