"""Gate evidence: a flagged supplier produces zero ERP writes.

Writes spikes/policy_gate/evidence.json. Evidence belongs in the repo, never
in a scratchpad — a previous gate's proof was lost that way.

Run: uv run python spikes/policy_gate/verify.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Running `uv run python spikes/policy_gate/verify.py` (rather than `-m`
# from pyproject.toml) puts spikes/policy_gate/ on sys.path[0], not the repo
# root, so `import app` fails without this. Same fix as spikes/thin_vertical.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.risk import assess_case, load_policy  # noqa: E402

SCENARIOS = [
    (
        "sanctioned_entity_is_blocked",
        {"reachable": True, "flagged": ["syn-co-001"], "candidates": [
            {"id": "syn-co-001", "score": 1.0, "match": True}]},
        "blocked",
    ),
    (
        "decoy_near_match_is_parked_for_review",
        {"reachable": True, "flagged": [], "candidates": [
            {"id": "syn-co-008", "score": 0.526, "match": False}]},
        "review",
    ),
    (
        "unreachable_screening_is_parked_not_cleared",
        {"reachable": False, "flagged": [], "candidates": []},
        "review",
    ),
    (
        "clean_supplier_is_cleared",
        {"reachable": True, "flagged": [], "candidates": [
            {"id": "syn-co-100", "score": 0.11, "match": False}]},
        "clear",
    ),
]


def main() -> int:
    policy = load_policy()
    case = {"case_id": "EVIDENCE", "event_type": "new_supplier_packet", "supplier": "Acme"}
    checks = []

    for name, screening, expected in SCENARIOS:
        verdict = assess_case(screening=screening, case=case)
        checks.append(
            {
                "name": name,
                "expected_band": expected,
                "actual_band": verdict.band,
                "score": verdict.score,
                "factors_fired": [f.id for f in verdict.factors_fired],
                "passed": verdict.band == expected,
            }
        )

    passed = sum(1 for c in checks if c["passed"])
    evidence = {
        "gate": "policy_risk_node",
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "thresholds": policy.thresholds.model_dump(),
        "result": "PASS" if passed == len(checks) else "FAIL",
        "passed": f"{passed}/{len(checks)}",
        "checks": checks,
    }

    out = Path(__file__).parent / "evidence.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
