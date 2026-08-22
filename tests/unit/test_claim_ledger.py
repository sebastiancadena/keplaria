"""The claim ledger's resolver, checker, and renderer.

A public number is only as good as the run that produced it. This suite
protects the one property the ledger exists for: that a number in the prose
which no longer matches its evidence file is reported as a mismatch rather
than read past. Every test binds a claim to a real file on disk -- the
resolver's whole job is reading them, so mocking the read would test nothing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "claim_ledger.py"
_SPEC = importlib.util.spec_from_file_location("claim_ledger", _PATH)
ledger = importlib.util.module_from_spec(_SPEC)
# Registered before exec: `dataclasses` resolves annotations through
# sys.modules, so a module loaded purely by spec cannot define one.
sys.modules["claim_ledger"] = ledger
_SPEC.loader.exec_module(ledger)


def _evidence(root: Path, rel: str, payload: dict) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_resolves_a_dotted_path_and_renders_it_as_prose(tmp_path):
    _evidence(tmp_path, "spikes/judge_run/evidence.json", {"machine_seconds": 85.14})
    claim = ledger.Claim(
        id="judge_run_machine_seconds",
        claim="One deployed run of the whole loop",
        evidence="spikes/judge_run/evidence.json",
        path="machine_seconds",
        render="{:.1f} s",
    )

    assert ledger.resolve(claim, root=tmp_path) == "85.1 s"


def test_reports_a_mismatch_when_the_prose_no_longer_matches_the_evidence(tmp_path):
    _evidence(tmp_path, "spikes/judge_run/evidence.json", {"machine_seconds": 85.14})
    (tmp_path / "README.md").write_text(
        "One deployed run of that whole loop: **61.5 s of machine time**.\n"
    )
    claim = ledger.Claim(
        id="judge_run_machine_seconds",
        claim="One deployed run of the whole loop",
        evidence="spikes/judge_run/evidence.json",
        path="machine_seconds",
        render="{:.1f} s",
        appears_in=("README.md",),
    )

    result = ledger.check(claim, root=tmp_path)

    assert result.ok is False
    assert result.expected == "85.1 s"
    assert result.missing_from == ("README.md",)


def test_loads_claims_from_toml_including_the_surfaces_each_appears_on(tmp_path):
    (tmp_path / "claims.toml").write_text(
        """
[[claim]]
id = "judge_run_machine_seconds"
claim = "One deployed run of the whole loop"
evidence = "spikes/judge_run/evidence.json"
path = "machine_seconds"
render = "{:.1f} s"
appears_in = ["README.md", "docs/devpost.md"]
qualifier = "machine time only; the human approval is timed separately"
"""
    )

    claims = ledger.load(tmp_path / "claims.toml")

    assert len(claims) == 1
    assert claims[0].id == "judge_run_machine_seconds"
    assert claims[0].appears_in == ("README.md", "docs/devpost.md")
    assert claims[0].qualifier.startswith("machine time only")


def test_a_manual_claim_is_reported_as_manual_with_its_reason_never_as_a_pass(tmp_path):
    (tmp_path / "claims.toml").write_text(
        """
[[claim]]
id = "credit_headroom"
claim = "About twice the headroom needed against the expiring credit"
verify = "manual"
reason = "a rounded comparison, not a value any evidence file states"
appears_in = ["README.md"]
"""
    )
    claim = ledger.load(tmp_path / "claims.toml")[0]

    result = ledger.check(claim, root=tmp_path)

    assert result.status == "manual"
    assert result.ok is True
    assert "rounded comparison" in result.detail


def test_a_number_whose_copy_is_not_written_yet_is_pending_not_a_pass(tmp_path):
    _evidence(tmp_path, "spikes/lifecycle/evidence.json", {"enforced_hold_days": 31})
    claim = ledger.Claim(
        id="enforced_hold_days",
        claim="Days a non-compliant supplier was actually held from purchasing",
        evidence="spikes/lifecycle/evidence.json",
        path="enforced_hold_days",
        render="{} days",
        appears_in=(),
        reason="held until the lifecycle copy is rewritten",
    )

    result = ledger.check(claim, root=tmp_path)

    assert result.status == "pending"
    assert result.ok is True
    assert result.expected == "31 days"
    assert "held until" in result.detail


def test_a_claim_held_back_with_no_reason_fails_the_run(tmp_path):
    """A deliberate hold says why. An unexplained one is indistinguishable from
    a claim that was quietly abandoned, which is the failure this tool exists to
    prevent — so it is a failure, not a soft status."""
    _evidence(tmp_path, "spikes/lifecycle/evidence.json", {"enforced_hold_days": 31})
    claim = ledger.Claim(
        id="enforced_hold_days",
        claim="Days a non-compliant supplier was actually held from purchasing",
        evidence="spikes/lifecycle/evidence.json",
        path="enforced_hold_days",
        render="{} days",
        appears_in=(),
    )

    result = ledger.check(claim, root=tmp_path)

    assert result.status == "pending_unexplained"
    assert result.ok is False


def test_the_rendered_page_states_each_value_its_evidence_and_its_qualifier(tmp_path):
    _evidence(tmp_path, "spikes/manual_baseline/evidence.json", {"steps_eliminated": 19})
    (tmp_path / "README.md").write_text("19 steps of 20 are removed by the run.\n")
    claims = (
        ledger.Claim(
            id="manual_steps_eliminated",
            claim="Manual steps the run removes",
            evidence="spikes/manual_baseline/evidence.json",
            path="steps_eliminated",
            render="{}",
            appears_in=("README.md",),
            qualifier="author-timed, not practitioner-reviewed",
        ),
    )

    page = ledger.render_markdown(claims, root=tmp_path)

    assert "Manual steps the run removes" in page
    assert "](../../spikes/manual_baseline/evidence.json)" in page
    assert "author-timed, not practitioner-reviewed" in page
    assert "generated" in page.lower()


def _stale_repo(tmp_path) -> Path:
    _evidence(tmp_path, "spikes/judge_run/evidence.json", {"machine_seconds": 85.14})
    (tmp_path / "README.md").write_text("One deployed run: **61.5 s of machine time**.\n")
    toml = tmp_path / "claims.toml"
    toml.write_text(
        """
[[claim]]
id = "judge_run_machine_seconds"
claim = "One deployed run of the whole loop"
evidence = "spikes/judge_run/evidence.json"
path = "machine_seconds"
render = "{:.1f} s"
appears_in = ["README.md"]
"""
    )
    return toml


def test_the_command_exits_non_zero_when_a_public_number_has_gone_stale(tmp_path, capsys):
    toml = _stale_repo(tmp_path)

    code = ledger.main(["--check", "--ledger", str(toml), "--root", str(tmp_path)])

    assert code == 1
    printed = capsys.readouterr().out
    assert "85.1 s" in printed
    assert "README.md" in printed


def test_the_command_exits_zero_when_the_prose_states_what_the_evidence_says(tmp_path):
    toml = _stale_repo(tmp_path)
    (tmp_path / "README.md").write_text("One deployed run: **85.1 s of machine time**.\n")

    code = ledger.main(["--check", "--ledger", str(toml), "--root", str(tmp_path)])

    assert code == 0


def test_a_count_claim_resolves_to_the_number_of_entries_in_the_evidence(tmp_path):
    _evidence(
        tmp_path,
        "spikes/core_contracts/evidence.json",
        {"criteria": [{"id": f"c{i}"} for i in range(9)]},
    )
    claim = ledger.Claim(
        id="core_contracts_count",
        claim="Contracts re-verified at capture time",
        evidence="spikes/core_contracts/evidence.json",
        path="criteria",
        render="{}",
        length=True,
    )

    assert ledger.resolve(claim, root=tmp_path) == "9"


def test_a_count_written_as_a_word_is_found_however_the_prose_capitalises_it(tmp_path):
    _evidence(
        tmp_path,
        "spikes/core_contracts/evidence.json",
        {"criteria": [{"id": f"c{i}"} for i in range(9)]},
    )
    (tmp_path / "README.md").write_text("Nine contracts, re-executed at capture time.\n")
    claim = ledger.Claim(
        id="core_contracts_count",
        claim="Contracts re-verified at capture time",
        evidence="spikes/core_contracts/evidence.json",
        path="criteria",
        render="{} contracts",
        length=True,
        words=True,
        appears_in=("README.md",),
    )

    result = ledger.check(claim, root=tmp_path)

    assert result.expected == "nine contracts"
    assert result.status == "ok"


def test_a_numeric_path_segment_indexes_into_a_list_of_metrics(tmp_path):
    _evidence(
        tmp_path,
        "spikes/domain_evals/evidence.json",
        {"summary_metrics": [{"num_cases_total": 24, "mean_score": 1.0}]},
    )
    claim = ledger.Claim(
        id="domain_eval_cases",
        claim="Graded domain cases, all passing",
        evidence="spikes/domain_evals/evidence.json",
        path="summary_metrics.0.num_cases_total",
        render="{0}/{0}",
    )

    assert ledger.resolve(claim, root=tmp_path) == "24/24"


def test_a_claim_whose_evidence_file_is_gone_fails_and_names_the_missing_file(tmp_path):
    """Evidence gets deleted, and whatever deletes it never looks wrong at the time.

    A citation to a file that no longer exists must fail loudly. Crashing would
    be worse than useless: the caller cannot tell a deleted proof from a broken
    checker, and would report "could not run" over a claim that lost its source.
    """
    (tmp_path / "README.md").write_text("One deployed run: **85.1 s of machine time**.\n")
    claim = ledger.Claim(
        id="run_machine_seconds",
        claim="Machine time for one deployed run",
        evidence="spikes/judge_run/evidence.json",
        path="machine_seconds.total",
        render="{:.1f} s of machine time",
        appears_in=("README.md",),
    )

    result = ledger.check(claim, root=tmp_path)

    assert result.ok is False
    assert result.status == "no_evidence"
    assert "spikes/judge_run/evidence.json" in result.detail


def test_the_check_fails_when_the_generated_page_no_longer_matches_the_data(tmp_path):
    """The page is judge-facing, so it drifting is the same defect as prose drifting.

    Editing claims.toml and forgetting to re-render leaves a published table
    that disagrees with the check that vouches for it. Nothing else would
    notice: every claim still verifies.
    """
    toml = _stale_repo(tmp_path)
    (tmp_path / "README.md").write_text("One deployed run: **85.1 s of machine time**.\n")
    page = tmp_path / "claims.md"
    page.write_text("# Claim → source ledger\n\nhand-edited, and now out of date\n")

    code = ledger.main(
        ["--check", "--ledger", str(toml), "--root", str(tmp_path), "--page", str(page)]
    )

    assert code == 1
