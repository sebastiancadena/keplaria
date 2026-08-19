"""Coverage for the deterministic domain eval metric, loaded by path
since `tests/eval/` is not an importable package.

Each test builds a minimal grading instance around a case_id and a
synthetic post-run outcome, then checks the metric scores it the way
its own docstring says it should.

Every outcome must carry `model_agents`, because the metric grades it for
every case. The last group of tests covers that check itself, plus the
drift the suite is most exposed to: a case added to the dataset with no
branch, no exposure entry, or no slot in the seed's wipe list — the third
of which would leave a previous run's state in place and quietly change
what the case measures.
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
        "model_agents": ["compliance_agent", "mission_coordinator"],
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
        "model_agents": ["mission_coordinator"],
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
        "model_agents": ["mission_coordinator"],
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
        "model_agents": ["evidence_agent", "mission_coordinator"],
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
        "model_agents": ["mission_coordinator"],
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


# --- the model-exposure check, and the drift it is there to catch --------


def _clock_outcome(**overrides) -> dict:
    outcome = {
        "phase": "no_action",
        "routing": None,
        "policy": {"band": "clear", "factors_fired": []},
        "certificate": {"expiry_date": "2027-01-01"},
        "lifecycle": {"state": "active", "cycle": 1, "last_reason": "NOT_DUE"},
        "commands": [],
        "model_agents": [],
    }
    outcome.update(overrides)
    return outcome


def test_a_clock_case_passes_only_when_no_agent_ran():
    result = evaluate(_instance("EVAL-CLK-RENEW-EARLY", _clock_outcome()))
    assert result["score"] == 1.0


def test_a_clock_case_fails_when_the_coordinator_ran():
    outcome = _clock_outcome(model_agents=["mission_coordinator"])
    result = evaluate(_instance("EVAL-CLK-RENEW-EARLY", outcome))
    assert result["score"] == 0.0
    assert "mission_coordinator" in result["explanation"]


def test_a_tainted_case_fails_when_the_evidence_agent_saw_the_document():
    """The claim is that a tainted document cannot reach a model at all.
    Asserting the stored route would not catch an agent that ran anyway."""
    outcome = _injected_outcome()
    outcome["model_agents"] = ["evidence_agent", "mission_coordinator"]
    result = evaluate(_instance("EVAL-INJECT", outcome))
    assert result["score"] == 0.0
    assert "evidence_agent" in result["explanation"]


def test_a_case_fails_when_an_expected_agent_did_not_run():
    outcome = _blocked_outcome()
    outcome["model_agents"] = ["mission_coordinator"]
    result = evaluate(_instance("EVAL-SCREEN-HIT", outcome))
    assert result["score"] == 0.0
    assert "compliance_agent" in result["explanation"]


def test_a_trace_without_model_agents_fails_rather_than_passing_silently():
    """An older trace file has no model_agents key. That must read as
    ungraded, not as satisfied — the whole point of the check is that it
    can fail."""
    outcome = _blocked_outcome()
    del outcome["model_agents"]
    result = evaluate(_instance("EVAL-SCREEN-HIT", outcome))
    assert result["score"] == 0.0
    assert "model_agents" in result["explanation"]


def _dataset_case_ids() -> list[str]:
    dataset = json.loads(
        (Path(__file__).resolve().parent.parent / "eval" / "datasets"
         / "domain-dataset.json").read_text()
    )
    return [
        json.loads(c["prompt"]["parts"][0]["text"])["case_id"]
        for c in dataset["eval_cases"]
    ]


def test_every_dataset_case_has_a_branch_and_an_exposure_expectation():
    """A case with no branch scores zero as 'unknown'; a case with no
    exposure entry scores zero as 'not declared'. Both are silent until
    the suite runs for real, which costs a live Gemini pass to discover."""
    for case_id in _dataset_case_ids():
        result = evaluate(_instance(case_id, {"phase": None, "model_agents": []}))
        assert "unknown eval case_id" not in result["explanation"], case_id
        assert "no model-exposure expectation" not in result["explanation"], case_id


def test_the_seed_wipes_exactly_the_cases_the_dataset_runs():
    """A dataset case missing from the seed's wipe list keeps the previous
    run's state, so it silently stops measuring what it claims to."""
    import ast

    source = (Path(__file__).resolve().parent.parent / "eval" / "seed.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "EVAL_CASE_IDS" for t in node.targets
        ):
            seeded = [ast.literal_eval(e) for e in node.value.elts]
            break
    else:
        raise AssertionError("EVAL_CASE_IDS not found in tests/eval/seed.py")

    assert sorted(seeded) == sorted(_dataset_case_ids())
