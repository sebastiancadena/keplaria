"""Verify every core contract this system claims, then record the result.

This harness deliberately does NOT re-run the closed loop. `spikes/judge_run`
already does that, and duplicating it here would buy a second copy of the same
evidence rather than a new fact. What this adds is the thing no individual
spike could: a single artifact asserting that EVERY contract the system claims
is currently proven, and — more importantly — failing loudly when one is not.

The manifest is self-checking on purpose. A hand-written table mapping
criteria to proofs is a claim about the repo; this one re-executes the cited
pytest node ids and re-reads the cited evidence files on every run, so a test
that was deleted, renamed, or turned red demotes its criterion instead of
silently continuing to look green. That failure mode is not hypothetical: the
project has already shipped tests that passed while proving nothing.

Exits non-zero when any criterion is unproven. A green run IS the evidence;
there is no separate hand-maintained pass list to drift out of date.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Each criterion states the contract, then the proof. `tests`
# rows are re-run; `spike` rows are re-read and their verdict re-checked;
# `gap` rows are criteria with no proof yet and fail the run by construction.
CRITERIA = [
    {
        "id": "closed_loop",
        "requirement": (
            "One harness-driven closed loop: event to ERP, renewal request, "
            "overdue hold, renewed evidence, hold release"
        ),
        "spike": "judge_run",
        "note": (
            "Two suppliers, not one: a review-band supplier carries the approval "
            "beat and a clear-band supplier carries the lifecycle, because "
            "assess_risk carries a stored band forward and a parked case re-parks "
            "on every later event. spikes/lifecycle holds the earlier five-step "
            "deployed run of the same loop."
        ),
    },
    {
        "id": "duplicate_and_out_of_order_events",
        "requirement": "Duplicate and out-of-order events are refused",
        "tests": [
            "tests/unit/test_inbox.py::test_duplicate_event_is_rejected_and_does_not_bump_version",
            "tests/unit/test_inbox.py::test_stale_event_is_rejected",
        ],
        "note": (
            "Also proven on deployed resources: the thin-vertical replay left "
            "attempts=1 and case_version=1."
        ),
    },
    {
        "id": "provenance_failure",
        "requirement": "An extraction that is not grounded in the document is refused",
        "tests": [
            "tests/unit/test_grounding.py::test_a_mismatched_checksum_is_rejected",
            "tests/unit/test_grounding.py::test_a_span_absent_from_the_document_is_rejected",
            "tests/unit/test_grounding.py::test_a_page_out_of_range_is_rejected",
            "tests/unit/test_grounding.py::test_a_schema_valid_but_unsupported_expiry_is_rejected",
            "tests/unit/test_graph_wiring.py::test_ungrounded_evidence_retries_once_then_quarantines",
        ],
    },
    {
        "id": "injection_refusal",
        "requirement": "A tainted document cannot produce an ERP write",
        "tests": [
            "tests/unit/test_injection.py::test_the_planted_fixture_is_tainted",
            "tests/unit/test_injection.py::test_a_clean_certificate_is_not_tainted",
            "tests/unit/test_grounding.py::test_grounding_accepts_an_injection_obedient_extraction",
        ],
        "note": (
            "Detection is a heuristic over a representative fixture, NOT a general "
            "prompt-injection defence; a rephrased payload passes. Enforcement is "
            "the exact claim: a tainted document never reaches an agent-resolvable "
            "state key and DOCUMENT_INJECTION forces the deterministic gate to "
            "blocked. The grounding test pins that grounding is deliberately NOT "
            "the injection control."
        ),
    },
    {
        "id": "stale_and_double_approval",
        "requirement": "A stale or replayed human approval is refused",
        "tests": [
            "tests/unit/test_approvals.py::test_replaying_the_same_approval_is_rejected_as_a_duplicate",
            "tests/unit/test_approvals.py::test_an_approval_against_an_older_version_is_rejected_as_stale",
            "tests/unit/test_approvals.py::test_a_replay_is_reported_as_duplicate_even_after_the_case_moved_on",
            "tests/unit/test_approval_release.py::test_the_approval_is_replayable_without_a_second_erp_write",
        ],
        "note": (
            "The release path is also proven on deployed resources by "
            "spikes/hitl_release: a signed-in reviewer released a parked case "
            "through IAP into a real ERP write."
        ),
    },
    {
        "id": "forbidden_agent_tool_edges",
        "requirement": "An agent cannot reach a tool policy did not permit",
        "tests": [
            "tests/unit/test_graph_wiring.py::test_the_evidence_agent_holds_no_operational_tools",
            "tests/unit/test_graph_wiring.py::test_the_compliance_agent_holds_no_operational_tools",
            "tests/unit/test_nodes_routing.py::test_unknown_agent_name_is_blocked_not_skipped",
            "tests/unit/test_nodes_routing.py::test_unknown_event_type_is_blocked",
            "tests/unit/test_graph_wiring.py::test_a_clock_event_never_reaches_an_llm_agent",
        ],
    },
    {
        "id": "one_erp_write_after_retry",
        "requirement": "A retried command leaves exactly one ERP record",
        "tests": [
            "tests/unit/test_commands.py::test_claim_after_success_is_refused_and_returns_the_external_id",
            "tests/unit/test_commands.py::test_claim_after_failure_is_reacquired_and_counts_attempts",
            "tests/unit/test_commands.py::test_execution_attempts_are_counted_separately_from_claims",
        ],
        "live": "retried_erp_write_is_singular",
        "note": (
            "The deployed half is the DLQ sweep probe: its clear_hold command "
            "failed once (execution_attempts is written only by record_failure), "
            "was re-driven by the deployed sweep, and reached done six seconds "
            "later. It is the ONLY command among 48 ever written that failed and "
            "then succeeded — every other failure ran to the cap and parked dead, "
            "which is the bounded-retry contract working, not a second example. "
            "spikes/retry_503 is deliberately NOT cited here: its single "
            "downstream write is a redacted derivative, not an ERP write."
        ),
    },
    {
        "id": "category_proof_visible",
        "requirement": "Agent cataloging is externally observable",
        "spike": "agent_runtime",
        "note": (
            "Fleet. Agent Registry auto-registration observed at deploy time with "
            "no publish step (criterion 3 of the runtime spike). The versioned "
            "first-party catalog is retained as the documented fallback and is "
            "independently submission-eligible under the organizer ruling on "
            "forum 44797."
        ),
    },
    {
        "id": "eval_cases",
        "requirement": "The graded domain eval suite runs and passes",
        "spike": "domain_evals",
        "note": "Deterministic domain_case_pass metric graded by agents-cli eval grade.",
    },
]


def _spike_verdict(name: str) -> tuple[bool, str]:
    """Re-read a spike's committed evidence and decide whether it still passes.

    Each spike wrote its own shape before there was a house style, so this
    tolerates all of them rather than rewriting history to match.
    """
    path = ROOT / "spikes" / name / "evidence.json"
    if not path.exists():
        return False, f"{path.relative_to(ROOT)} is missing"
    data = json.loads(path.read_text())

    for key in ("result", "verdict"):
        value = data.get(key)
        if isinstance(value, str):
            return value.upper() == "PASS", f"{key}={value}"

    if isinstance(data.get("passed"), bool):
        return data["passed"], f"passed={data['passed']}"

    criteria = data.get("criteria")
    if isinstance(criteria, dict) and criteria:
        bools = [v for v in criteria.values() if isinstance(v, bool)]
        if bools:
            return all(bools), f"{sum(bools)}/{len(bools)} criteria true"

    # The eval export carries no verdict field — grade it on its own metric.
    for metric in data.get("summary_metrics") or []:
        if metric.get("metric_name") == "domain_case_pass":
            total = metric.get("num_cases_total") or 0
            mean = metric.get("mean_score")
            ok = total >= 8 and mean == 1.0
            return ok, f"{total} cases, mean_score={mean}"

    return False, "no recognisable verdict in evidence"


SWEEP_CASE = "DLQ-SWEEP-43CDC293"
SWEEP_COMMAND = f"{SWEEP_CASE}:clear_hold:c1"
SWEEP_SUPPLIER = "DLQ Sweep Probe SAS"


def retried_erp_write_is_singular() -> tuple[bool, str]:
    """A command that failed, retried, and succeeded left exactly ONE record.

    Read-only against both deployed Firestore and the live ERP — it inspects
    the outcome of a run that already happened rather than causing a new one,
    so re-running this harness never adds ERP residue.

    The unit tests above prove the ledger REFUSES a re-claim after success.
    This proves the consequence actually held in the ERP: one supplier, not a
    duplicate created by the second execution.
    """
    from app.executor.frappe import frappe_client
    from app.state.firestore import CASES, OUTBOX, get_client

    db = get_client(database="(default)")
    snap = (
        db.collection(CASES).document(SWEEP_CASE)
        .collection(OUTBOX).document(SWEEP_COMMAND).get()
    )
    if not snap.exists:
        return False, f"command {SWEEP_COMMAND} is absent from the deployed ledger"
    command = snap.to_dict() or {}
    attempts = int(command.get("execution_attempts") or 0)
    if command.get("status") != "done":
        return False, f"command status is {command.get('status')!r}, expected done"
    if attempts < 1:
        return False, (
            "command never failed, so this proves nothing about retry "
            "(execution_attempts is written only by record_failure)"
        )

    with frappe_client() as client:
        response = client.get(
            "/api/resource/Supplier",
            params={
                "filters": json.dumps([["name", "=", SWEEP_SUPPLIER]]),
                "fields": '["name","on_hold"]',
                "limit_page_length": 100,
            },
        )
        response.raise_for_status()
        records = response.json()["data"]

    if len(records) != 1:
        return False, f"{len(records)} ERP records named {SWEEP_SUPPLIER!r}, expected exactly 1"
    if records[0].get("on_hold"):
        return False, "the retried clear_hold did not take effect (on_hold is still set)"
    return True, (
        f"failed {attempts}x then done; exactly 1 ERP record, hold cleared"
    )


LIVE_CHECKS = {"retried_erp_write_is_singular": retried_erp_write_is_singular}


def _run_tests(node_ids: list[str]) -> tuple[bool, str]:
    """Re-run the cited tests. Their passing now is the point, not their names."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", *node_ids],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = [line for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode == 0, tail[-1] if tail else "no pytest output"


def main() -> int:
    results = []
    for entry in CRITERIA:
        checks = []
        proven = True

        if entry.get("tests"):
            ok, detail = _run_tests(entry["tests"])
            checks.append({"kind": "tests", "ok": ok, "detail": detail,
                           "node_ids": entry["tests"]})
            proven = proven and ok

        if entry.get("spike"):
            ok, detail = _spike_verdict(entry["spike"])
            checks.append({"kind": "spike", "ok": ok, "detail": detail,
                           "evidence": f"spikes/{entry['spike']}/evidence.json"})
            proven = proven and ok

        if entry.get("live"):
            try:
                ok, detail = LIVE_CHECKS[entry["live"]]()
            except Exception as exc:  # credentials, network, deleted resource
                ok, detail = False, f"live check raised {type(exc).__name__}: {exc}"
            checks.append({"kind": "live", "ok": ok, "detail": detail,
                           "check": entry["live"]})
            proven = proven and ok

        if entry.get("gap"):
            checks.append({"kind": "gap", "ok": False, "detail": entry["gap"]})
            proven = False

        row = {
            "id": entry["id"],
            "requirement": entry["requirement"],
            "proven": proven,
            "checks": checks,
        }
        if entry.get("note"):
            row["note"] = entry["note"]
        results.append(row)

        mark = "PASS" if proven else "FAIL"
        print(f"[{mark}] {entry['id']}")
        for check in checks:
            print(f"        {check['kind']}: {check['detail'][:150]}")

    unproven = [r["id"] for r in results if not r["proven"]]
    evidence = {
        "spike": "core_contracts",
        "scope": "core contracts",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "result": "FAIL" if unproven else "PASS",
        "unproven": unproven,
        "what_this_proves": (
            "Every criterion listed here was re-verified at capture time: "
            "cited tests were re-executed in this run, and cited spike evidence "
            "was re-read and its verdict re-checked. This is a verification pass "
            "over work already done, not a fresh end-to-end execution."
        ),
        "not_proven_here": [
            "The closed loop is not re-executed by this harness; spikes/judge_run"
            " owns that and its run is cited, not repeated.",
            "The rejection path and apply_hold-on-reject remain unexercised"
            " through the deployed UI; only approval-and-release is proven.",
            "Injection detection is a fixture heuristic, not a general defence;"
            " only the enforcement consequence is exact.",
            "A green run here says the named proofs hold. It says nothing about"
            " contracts this list does not name.",
        ],
        "criteria": results,
    }
    (HERE / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")

    print()
    if unproven:
        print(f"RESULT: FAIL — {len(unproven)} unproven: {', '.join(unproven)}")
        return 1
    print(f"RESULT: PASS — {len(results)}/{len(results)} criteria proven")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
