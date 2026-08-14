# Deterministic Policy and Risk Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sanctions screening actually gate the ERP write, so a flagged supplier is never onboarded.

**Architecture:** A new pure-code module (`app/risk.py`) scores a case against a versioned JSON policy fixture and returns one of three bands — `clear`, `review`, `blocked`. A new graph node (`assess_risk`) sits between screening and the command queue and routes on that band; `clear` queues the ERP command, `review` parks the case, `blocked` quarantines it. The Cloud Run executor re-reads the persisted verdict and refuses to drain a command whose case is not `clear`.

**Tech Stack:** Python 3.13, ADK 2.5.0 (`google.adk.workflow.Workflow`), Pydantic v2, Firestore, pytest, OpenTelemetry → Cloud Trace.

**Spec:** `docs/superpowers/specs/2026-08-14-policy-risk-node-design.md`

## Global Constraints

- **`assess()` never raises.** Every failure path returns a verdict with a reason code. A raising gate becomes Pub/Sub retry pressure under R31 (Agent Runtime allows 1 concurrent query, 30/min).
- **Fixture loading is lazy and cached, never at module import.** An import-time failure presents as a log-less "failed to start and cannot serve traffic" on Agent Runtime.
- **Band values are exactly `clear`, `review`, `blocked`** — these are also the ADK route names in `app/agent.py`.
- **The `review` terminal writes phase `awaiting_approval`** — the term `strategy/architecture-contracts.md` reserves. Never describe it as a live pause; `RequestInput` is not in this graph.
- **Traces carry factor **ids** only.** The values that triggered a factor go to Firestore, never to a span attribute or log — the data handling contract keeps entity-identifying values out of telemetry.
- **The executor guard is refusal-only.** It may stop a write; it may never authorize one, and it may never mark a command `DONE` or `FAILED`.
- **Zero outbox writes on `review` and `blocked`.** Neither terminal calls `claim_command`.
- **Tests run hermetically by default:** `uv run pytest`. Live tests are opt-in via `-m live`. Do not add live markers in this plan.
- **No `pip install`. Use `uv`.** No new third-party dependencies are needed.

---

### Task 1: The policy fixture and the pure risk module

**Files:**

- Create: `policy/supplier_risk.v1.json`
- Create: `app/risk.py`
- Test: `tests/unit/test_risk.py`

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces, all imported by Task 2 and Task 4:
  - `CLEAR = "clear"`, `REVIEW = "review"`, `BLOCKED = "blocked"` (module-level `str` constants)
  - `class Policy(BaseModel)` with `policy_id: str`, `policy_version: int`, `thresholds: Thresholds`, `factors: list[Factor]`
  - `class RiskVerdict(BaseModel)` with `policy_id: str`, `policy_version: int`, `score: float`, `band: str`, `factors_fired: list[FiredFactor]`, `reasons: list[str]`
  - `class PolicyLoadError(ValueError)`
  - `load_policy(path: Path | None = None) -> Policy` — validates, raises `PolicyLoadError`
  - `assess(policy: Policy, *, screening: dict | None, case: dict) -> RiskVerdict` — pure, total
  - `assess_case(*, screening: dict | None, case: dict, path: Path | None = None) -> RiskVerdict` — lazy-loads and caches the policy; returns a `blocked` verdict with reason `POLICY_UNAVAILABLE` if loading fails
  - `reset_policy_cache() -> None` — test hook
- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_risk.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_risk.py -v`

Expected: collection error — `ModuleNotFoundError: No module named 'app.risk'`.

- [ ] **Step 3: Create the policy fixture**

Create `policy/supplier_risk.v1.json`:

```json
{
  "policy_id": "supplier_risk",
  "policy_version": 1,
  "thresholds": { "review": 0.20, "block": 0.60 },
  "factors": [
    {
      "id": "SANCTIONS_MATCH",
      "weight": 0.70,
      "description": "yente returned a confirmed match for the legal entity.",
      "when": { "kind": "screening_match" }
    },
    {
      "id": "SUBTHRESHOLD_CANDIDATE",
      "weight": 0.25,
      "description": "A near-match a human should adjudicate.",
      "when": { "kind": "screening_candidate_above", "score": 0.50 }
    },
    {
      "id": "SCREENING_UNAVAILABLE",
      "weight": 0.30,
      "description": "Screening was attempted and the service did not answer.",
      "when": { "kind": "screening_unreachable" }
    }
  ]
}
```

Weights are load-bearing: `SANCTIONS_MATCH` (0.70) is at or above `block`, so a confirmed match blocks alone. `SUBTHRESHOLD_CANDIDATE` (0.25) and `SCREENING_UNAVAILABLE` (0.30) sit at or above `review` and below `block`, so each alone parks the case. Do not change these without revisiting the spec.

- [ ] **Step 4: Write the risk module**

Create `app/risk.py`:

```python
"""Deterministic policy and risk scoring — flight plan step 5.

The compliance-critical decision is code plus versioned data, never model
output. `assess` is a total function: it never raises, because a gate that
throws stops being a decision and becomes retry pressure (R31 — Agent Runtime
allows one concurrent query). Every failure path instead produces a verdict
carrying a reason code, so an auditable outcome always reaches the case
document.

The split that keeps the policy suppliable by a later BigQuery adapter without
an expression language: condition *kinds* are code in CONDITION_KINDS; the
fixture chooses kinds, supplies their parameters, and assigns weights.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

CLEAR = "clear"
REVIEW = "review"
BLOCKED = "blocked"

DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "policy" / "supplier_risk.v1.json"


class PolicyLoadError(ValueError):
    """The policy fixture is missing, malformed, or self-inconsistent."""


class Thresholds(BaseModel):
    review: float = Field(ge=0.0, le=1.0)
    block: float = Field(ge=0.0, le=1.0)


class Factor(BaseModel):
    id: str
    weight: float = Field(ge=0.0, le=1.0)
    description: str = ""
    when: dict


class Policy(BaseModel):
    policy_id: str
    policy_version: int
    thresholds: Thresholds
    factors: list[Factor]


class FiredFactor(BaseModel):
    id: str
    weight: float
    value: str


class RiskVerdict(BaseModel):
    policy_id: str
    policy_version: int
    score: float
    band: str
    factors_fired: list[FiredFactor] = []
    reasons: list[str] = []


# --- condition kinds -------------------------------------------------------
# Each returns (fired, value_description). `value_description` is persisted to
# Firestore only, never to a span attribute.

def _screening_match(params: dict, screening: dict) -> tuple[bool, str]:
    flagged = screening.get("flagged") or []
    if not flagged:
        return False, ""
    scores = {c.get("id"): c.get("score") for c in screening.get("candidates") or []}
    top = flagged[0]
    return True, f"{top} @ {scores.get(top, 0.0):.3f}"


def _screening_candidate_above(params: dict, screening: dict) -> tuple[bool, str]:
    floor = params["score"]
    above = [c for c in screening.get("candidates") or [] if (c.get("score") or 0.0) >= floor]
    if not above:
        return False, ""
    top = max(above, key=lambda c: c.get("score") or 0.0)
    return True, f"{top.get('id')} @ {top.get('score'):.3f}"


def _screening_unreachable(params: dict, screening: dict) -> tuple[bool, str]:
    if screening.get("reachable") is False:
        return True, screening.get("error") or "screening service did not answer"
    return False, ""


CONDITION_KINDS: dict[str, Callable[[dict, dict], tuple[bool, str]]] = {
    "screening_match": _screening_match,
    "screening_candidate_above": _screening_candidate_above,
    "screening_unreachable": _screening_unreachable,
}

_REQUIRED_PARAMS = {"screening_candidate_above": ("score",)}


# --- loading ---------------------------------------------------------------

def load_policy(path: Path | None = None) -> Policy:
    """Parse and validate the fixture. Raises PolicyLoadError on any problem.

    Validation lives here, not in `assess`, so an unregistered condition kind
    is impossible at decision time — which is what lets `assess` be total
    without swallowing genuine bugs.
    """
    target = Path(path) if path else DEFAULT_POLICY_PATH
    try:
        policy = Policy.model_validate_json(target.read_text())
    except (OSError, ValidationError, ValueError) as exc:
        raise PolicyLoadError(f"cannot load policy from {target}: {exc}") from exc

    if policy.thresholds.review > policy.thresholds.block:
        raise PolicyLoadError(
            f"threshold order: review {policy.thresholds.review} > block {policy.thresholds.block}"
        )

    for factor in policy.factors:
        kind = factor.when.get("kind")
        if kind not in CONDITION_KINDS:
            raise PolicyLoadError(f"factor {factor.id}: unregistered condition kind {kind!r}")
        for param in _REQUIRED_PARAMS.get(kind, ()):
            if not isinstance(factor.when.get(param), (int, float)):
                raise PolicyLoadError(f"factor {factor.id}: {kind} needs a numeric {param!r}")

    return policy


_CACHE: dict[str, Policy] = {}


def reset_policy_cache() -> None:
    """Test hook. Production never calls this."""
    _CACHE.clear()


def _cached_policy(path: Path | None) -> Policy:
    key = str(Path(path) if path else DEFAULT_POLICY_PATH)
    if key not in _CACHE:
        _CACHE[key] = load_policy(path)
    return _CACHE[key]


# --- assessment ------------------------------------------------------------

def _band(policy: Policy, score: float) -> str:
    if score >= policy.thresholds.block:
        return BLOCKED
    if score >= policy.thresholds.review:
        return REVIEW
    return CLEAR


def _is_malformed(screening: dict) -> bool:
    if not isinstance(screening.get("reachable"), bool):
        return True
    if not isinstance(screening.get("candidates", []), list):
        return True
    if not isinstance(screening.get("flagged", []), list):
        return True
    return False


def assess(policy: Policy, *, screening: dict | None, case: dict) -> RiskVerdict:
    """Score the case. Total — never raises.

    `screening is None` means screening was never required for this event type
    (the `skip` branch). That is deliberately NOT the same as a screening
    attempt that failed, which fires SCREENING_UNAVAILABLE.
    """
    if screening is not None and (not isinstance(screening, dict) or _is_malformed(screening)):
        return RiskVerdict(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            score=1.0,
            band=BLOCKED,
            reasons=["SCREENING_MALFORMED"],
        )

    fired: list[FiredFactor] = []
    if screening is not None:
        for factor in policy.factors:
            kind = factor.when["kind"]
            hit, value = CONDITION_KINDS[kind](factor.when, screening)
            if hit:
                fired.append(FiredFactor(id=factor.id, weight=factor.weight, value=value))

    score = min(1.0, round(sum(f.weight for f in fired), 6))
    return RiskVerdict(
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        score=score,
        band=_band(policy, score),
        factors_fired=fired,
    )


def assess_case(*, screening: dict | None, case: dict, path: Path | None = None) -> RiskVerdict:
    """Load the policy (lazily, cached) and assess. Total — never raises.

    Loading is deliberately not done at import: a malformed fixture failing at
    import presents on Agent Runtime as a log-less "failed to start and cannot
    serve traffic", which is the single most expensive failure mode this
    project has hit. Failing here is fail-closed and diagnosable.
    """
    try:
        policy = _cached_policy(path)
    except PolicyLoadError:
        return RiskVerdict(
            policy_id="unavailable",
            policy_version=0,
            score=1.0,
            band=BLOCKED,
            reasons=["POLICY_UNAVAILABLE"],
        )
    return assess(policy, screening=screening, case=case)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_risk.py -v`

Expected: 11 passed.

- [ ] **Step 6: Commit**

```bash
git add app/risk.py policy/supplier_risk.v1.json tests/unit/test_risk.py
git commit -m "feat(policy): deterministic risk scoring against a versioned fixture"
```

---

### Task 2: The `assess_risk` and `park_case` nodes

**Files:**

- Modify: `app/nodes.py` — extend `_record_outcome`, add `assess_risk` and `park_case`, pass `policy` from `quarantine_case` and `queue_supplier`
- Test: `tests/unit/test_nodes_risk.py`

**Interfaces:**

- Consumes from Task 1: `assess_case`, `CLEAR`, `REVIEW`, `BLOCKED`, `RiskVerdict`.
- Produces, used by Task 3:
  - `assess_risk(node_input, ctx) -> Event` — sets `state={"policy": <verdict dict>}` and `route` to the band string
  - `park_case(node_input, ctx) -> Event` — terminal for `review`; writes phase `awaiting_approval`; claims no command
  - `_record_outcome(db, case_id, phase, routing, screening, policy=None)` — new trailing optional argument
- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_nodes_risk.py`:

```python
"""Unit tests for the risk gate node and the review terminal.

assess_risk must route on the band and never on model output; park_case must
be a true terminal that claims no command. Together these close R30 at the
graph level: a flagged supplier can no longer reach queue_supplier.
"""

from __future__ import annotations

from app.nodes import assess_risk, park_case
from app.state.commands import get_command
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
    assert get_command(db, case_id, "create_supplier") is None


def test_park_case_persists_phase_and_verdict(db, case_id):
    verdict = {"policy_id": "supplier_risk", "policy_version": 1, "score": 0.25,
               "band": "review", "factors_fired": [], "reasons": []}
    ctx = _StubContext({"case": _case(case_id), "screening": _screening(), "policy": verdict})

    park_case(None, ctx)

    stored = db.collection(CASES).document(case_id).get().to_dict()
    assert stored["phase"] == "awaiting_approval"
    assert stored["policy"]["band"] == "review"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_nodes_risk.py -v`

Expected: `ImportError: cannot import name 'assess_risk' from 'app.nodes'`.

- [ ] **Step 3: Extend `_record_outcome` to carry the verdict**

In `app/nodes.py`, replace the `_record_outcome` signature and its `set()` call. The current signature is:

```python
def _record_outcome(db, case_id: str, phase: str, routing: dict | None, screening: dict | None) -> None:
```

Replace it with:

```python
def _record_outcome(
    db,
    case_id: str,
    phase: str,
    routing: dict | None,
    screening: dict | None,
    policy: dict | None = None,
) -> None:
```

and replace the final `db.collection(...).set(...)` call in that function with:

```python
    db.collection(CASES).document(case_id).set(
        {"phase": phase, "routing": routing, "screening": summary, "policy": policy},
        merge=True,
    )
```

Also update that function's docstring: the sentence beginning "The persisted `screening` block is a record of what yente returned, not a gate" is now false. Replace that paragraph with:

```python
    The persisted `policy` block is the authoritative record of the gate's
    decision. app.executor.runner re-reads it before draining a command, so
    this is not merely a projection — it is read back for enforcement.
```

- [ ] **Step 4: Add the imports**

At the top of `app/nodes.py`, alongside the existing `from app.policy import PolicyError, validate_route`, add:

```python
from app.risk import assess_case
```

- [ ] **Step 5: Add the `assess_risk` node**

Add to `app/nodes.py`, immediately after `screen_supplier`:

```python
def assess_risk(node_input, ctx: Context) -> Event:
    """The gate. Deterministic policy decides whether the ERP command may be queued.

    Routes on the band, never on model output. Reached from BOTH the screened
    branch and the skip branch, so no path to queue_supplier bypasses a
    verdict — which is what lets the executor treat a missing verdict as an
    anomaly to refuse rather than a state it must tolerate.

    Only factor IDs reach the span. The values that triggered them go to
    Firestore via _record_outcome: the data handling contract keeps
    entity-identifying values out of telemetry.
    """
    case = ctx.state.get("case", {})
    screening = ctx.state.get("screening")

    with tracer.start_as_current_span("assess_risk") as span:
        verdict = assess_case(screening=screening, case=case)
        span.set_attribute("keplaria.case_id", case.get("case_id", ""))
        span.set_attribute("keplaria.policy_version", verdict.policy_version)
        span.set_attribute("keplaria.risk_score", verdict.score)
        span.set_attribute("keplaria.risk_band", verdict.band)
        span.set_attribute(
            "keplaria.factors_fired", [f.id for f in verdict.factors_fired]
        )

    payload = verdict.model_dump()
    return Event(output=payload, state={"policy": payload}, route=verdict.band)
```

- [ ] **Step 6: Add the `park_case` terminal**

Add to `app/nodes.py`, immediately after `quarantine_case`:

```python
def park_case(node_input, ctx: Context) -> Event:
    """Terminal node for the `review` band — a case parked for a human.

    Zero writes: no command claim, no ERP call, exactly like quarantine_case.

    The phase is `awaiting_approval`, the term architecture-contracts.md
    reserves for a case parked pending a human decision. This is NOT a live
    pause — RequestInput is not in this graph. Day-8 Ground Control replaces
    this node with a real pause on the same branch.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    policy = ctx.state.get("policy")

    with tracer.start_as_current_span("park_case") as span:
        span.set_attribute("keplaria.case_id", case_id)
        span.set_attribute("keplaria.parked", True)
        _record_outcome(
            get_client(),
            case_id,
            "awaiting_approval",
            ctx.state.get("routing"),
            ctx.state.get("screening"),
            policy,
        )

    return Event(
        output={
            "status": "awaiting_approval",
            "case_id": case_id,
            "policy": policy,
            "routing": ctx.state.get("routing"),
        }
    )
```

- [ ] **Step 7: Pass the verdict from the existing terminals**

In `app/nodes.py`, `quarantine_case` currently calls:

```python
        _record_outcome(get_client(), case_id, "quarantined", routing, ctx.state.get("screening"))
```

Replace with:

```python
        _record_outcome(
            get_client(),
            case_id,
            "quarantined",
            routing,
            ctx.state.get("screening"),
            ctx.state.get("policy"),
        )
```

In `queue_supplier` there are two `_record_outcome` calls, one on the replay path (`phase="executed"`) and one on the queue path (`phase="queued"`). Add `ctx.state.get("policy")` as a sixth argument to both.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_nodes_risk.py tests/unit/test_nodes_routing.py -v`

Expected: all pass. `test_nodes_routing.py` is included because `_record_outcome` changed signature and `quarantine_case` is under test there.

- [ ] **Step 9: Commit**

```bash
git add app/nodes.py tests/unit/test_nodes_risk.py
git commit -m "feat(graph): add the assess_risk gate and the review terminal"
```

---

### Task 3: Rewire the graph

**Files:**

- Modify: `app/agent.py:73-94` — the `edges` list and the module docstring
- Test: `tests/integration/test_graph.py`

**Interfaces:**

- Consumes from Task 2: `assess_risk`, `park_case`.
- Produces: the wired graph. No new symbols.
- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_graph.py`:

```python
def test_flagged_supplier_never_reaches_the_command_queue(db, case_id):
    """R30 regression. The day-3 evidence run onboarded syn-co-001 under
    "passed": true; that must now be impossible."""
    from app.agent import root_agent

    edges = {}
    for edge in root_agent.edges:
        source = edge[0]
        target = edge[1]
        name = getattr(source, "__name__", str(source))
        edges[name] = target

    from app.nodes import assess_risk, park_case, queue_supplier, quarantine_case, screen_supplier

    assert edges["screen_supplier"] is assess_risk, "screening must feed the gate"
    assert edges["assess_risk"] == {
        "clear": queue_supplier,
        "review": park_case,
        "blocked": quarantine_case,
    }
    assert edges["apply_route"]["skip"] is assess_risk, "the skip branch must also pass the gate"
    assert edges["apply_route"]["screen"] is screen_supplier
    assert edges["apply_route"]["blocked"] is quarantine_case
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_graph.py::test_flagged_supplier_never_reaches_the_command_queue -v`

Expected: FAIL — `edges["screen_supplier"]` is `queue_supplier`, not `assess_risk`.

- [ ] **Step 3: Rewire the edges**

In `app/agent.py`, add `assess_risk` and `park_case` to the existing import from `app.nodes`, then replace the `edges` list with:

```python
    edges=[
        ("START", parse_case),
        (parse_case, coordinator),
        (coordinator, apply_route),
        # A routing-map chain element is this ADK version's syntax for a
        # conditional edge. "skip" goes to assess_risk rather than straight to
        # queue_supplier so that EVERY path to the command queue carries a
        # policy verdict — that invariant is what lets the executor refuse a
        # case with no verdict instead of having to tolerate one.
        (
            apply_route,
            {
                "screen": screen_supplier,
                "skip": assess_risk,
                "blocked": quarantine_case,
            },
        ),
        (screen_supplier, assess_risk),
        # The gate. Only "clear" reaches the command queue.
        (
            assess_risk,
            {
                "clear": queue_supplier,
                "review": park_case,
                "blocked": quarantine_case,
            },
        ),
    ],
```

- [ ] **Step 4: Update the module docstring**

In `app/agent.py`, the docstring's first paragraph currently reads:

```text
event → canonical parse → structured routing decision → validated branch →
deterministic command queue. The coordinator proposes a route; deterministic
policy code decides whether it is allowed, so no model output reaches a side
effect unvalidated.
```

Replace with:

```text
event → canonical parse → structured routing decision → validated branch →
sanctions screening → deterministic risk gate → command queue. The
coordinator proposes a route; deterministic policy code decides whether it is
allowed, and a second deterministic node decides whether the screened
supplier may be onboarded at all. No model output reaches a side effect
unvalidated, and a flagged supplier never reaches the command queue.
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`

Expected: all pass. Neither `tests/integration/test_agent.py` nor `test_server_e2e.py` asserts on the phase sequence today (only `tests/unit/test_nodes_routing.py:132` does, on `"quarantined"`, which this task does not change), so no test edits should be needed here. If one does fail, make it expect the new `assess_risk` hop — do not weaken the assertion to make it pass.

- [ ] **Step 6: Commit**

```bash
git add app/agent.py tests/integration/test_graph.py
git commit -m "feat(graph): route every path to the command queue through the risk gate"
```

---

### Task 4: The executor refusal guard

**Files:**

- Modify: `app/executor/runner.py` — module docstring, imports, and `execute_pending_commands`
- Test: `tests/integration/test_executor_runner.py`

**Interfaces:**

- Consumes from Task 1: `CLEAR`.
- Produces: a new result entry shape `{"action": str, "status": "refused_by_policy", "band": str | None, "policy_version": int | None}`.
- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_executor_runner.py`:

```python
def test_executor_refuses_a_case_whose_verdict_is_not_clear(db, case_id):
    """Backstop at the authorization boundary: a command queued under older
    state must not drain once the case is blocked. Refusal is not failure —
    the command stays PENDING and is never marked DONE or FAILED."""
    from app.executor.runner import execute_pending_commands
    from app.state.commands import PENDING, claim_command, get_command
    from app.state.firestore import CASES

    claim_command(db, case_id, "create_supplier", {"supplier_name": "Acme"})
    db.collection(CASES).document(case_id).set(
        {"policy": {"band": "blocked", "policy_version": 1}}, merge=True
    )

    results = execute_pending_commands(db, case_id)

    assert results == [
        {
            "action": "create_supplier",
            "status": "refused_by_policy",
            "band": "blocked",
            "policy_version": 1,
        }
    ]
    assert get_command(db, case_id, "create_supplier")["status"] == PENDING


def test_executor_refuses_a_case_with_no_verdict_at_all(db, case_id):
    """Every graph path now writes a verdict, so absence is an anomaly."""
    from app.executor.runner import execute_pending_commands
    from app.state.commands import PENDING, claim_command, get_command

    claim_command(db, case_id, "create_supplier", {"supplier_name": "Acme"})

    results = execute_pending_commands(db, case_id)

    assert results[0]["status"] == "refused_by_policy"
    assert results[0]["band"] is None
    assert get_command(db, case_id, "create_supplier")["status"] == PENDING
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/integration/test_executor_runner.py -v -k refuse`

Expected: FAIL — the executor attempts a real Frappe call and records a failure, or returns `[]`.

- [ ] **Step 3: Add the guard**

In `app/executor/runner.py`, add to the imports:

```python
from app.risk import CLEAR
```

Add this helper immediately above `execute_pending_commands`:

```python
def _policy_band(db, case_id: str) -> tuple[str | None, int | None]:
    """Read the gate's verdict off the case document.

    Returns (None, None) when the case or its policy block is absent — which
    every graph path now makes an anomaly, and which the caller refuses.
    """
    snap = db.collection(CASES).document(case_id).get()
    policy = ((snap.to_dict() or {}) if snap.exists else {}).get("policy") or {}
    return policy.get("band"), policy.get("policy_version")
```

Inside `execute_pending_commands`, immediately after the `outbox_ref = ...` line and before the `for snap in outbox_ref.stream():` loop, add:

```python
    band, policy_version = _policy_band(db, case_id)
    refused = band != CLEAR
```

Then inside the loop, immediately after the existing `if action != _CREATE_SUPPLIER: continue` line, add:

```python
        if refused:
            # Refusal-only: this guard can stop a write, never authorize one.
            # Deliberately NOT record_failure — a refusal is not a failure, and
            # the command must stay PENDING so that a later approval flipping
            # the verdict to `clear` lets the next drain execute it normally.
            results.append(
                {
                    "action": action,
                    "status": "refused_by_policy",
                    "band": band,
                    "policy_version": policy_version,
                }
            )
            continue
```

- [ ] **Step 4: Update the module docstring**

In `app/executor/runner.py`, append this paragraph to the module docstring:

```text
This module also re-reads the gate's verdict (`cases/{case_id}.policy`) before
draining and refuses any command whose case is not `clear`. That is a backstop,
not the primary enforcement: the graph's assess_risk branch is what stops a
flagged supplier, and in the happy path this guard never fires, because the
review and blocked terminals claim no command. It exists for the anomalous
paths — a duplicate-event redelivery draining a command queued under older
state, or a graph-wiring bug — and it matters because this process runs under
a different identity (the Cloud Run ingress) than the graph.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/integration/test_executor_runner.py -v`

Expected: all pass, including the pre-existing tests.

- [ ] **Step 6: Commit**

```bash
git add app/executor/runner.py tests/integration/test_executor_runner.py
git commit -m "feat(executor): refuse to drain commands for a case the gate did not clear"
```

---

### Task 5: Retract the advisory language and capture evidence

Landing the code without this task leaves the repository asserting the opposite of the truth in four places.

**Files:**

- Modify: `app/nodes.py` — the `queue_supplier` docstring
- Modify: `README.md:43-52` — the "Fail-closed routing" paragraph
- Modify: `README.md:30-32` — the pipeline diagram
- Modify: `spikes/thin_vertical/verify.py:30`
- Modify: `strategy/risk-register.md` — the R30 row
- Create: `spikes/policy_gate/verify.py`, `spikes/policy_gate/evidence.json`

**Interfaces:** none — documentation and evidence only.

- [ ] **Step 1: Rewrite the `queue_supplier` docstring**

In `app/nodes.py`, the `queue_supplier` docstring opens with a block beginning `IMPORTANT — screening does not gate this write.` and running to `...regardless of what screen_supplier found.` Replace that entire block with:

```python
    Reached only via the assess_risk gate's `clear` branch, so by the time
    this node runs the case already carries a policy verdict that permits an
    ERP command. A flagged or near-match supplier terminates at
    quarantine_case or park_case instead and never arrives here.
```

Leave the rest of the docstring — the PSC-I egress explanation and the authorization-boundary rationale — unchanged. Both are still true.

- [ ] **Step 2: Rewrite the README pipeline and gate paragraph**

In `README.md`, replace lines 30-32:

```text
  -> Agent Runtime graph: parse -> LLM coordinator routing proposal ->
     deterministic policy validation (app/policy.py) -> yente screening
     over PSC-I -> queue ERP command
```

with:

```text
  -> Agent Runtime graph: parse -> LLM coordinator routing proposal ->
     deterministic route validation (app/policy.py) -> yente screening
     over PSC-I -> deterministic risk gate (app/risk.py) -> queue ERP
     command, or park/quarantine the case
```

Then replace the whole "Fail-closed routing" paragraph (lines 43-52) with:

```markdown
**Two deterministic gates, both fail-closed.** The LLM coordinator only
proposes a route; `app/policy.py` decides whether it is permitted, and a
refused proposal routes to a `quarantine_case` terminal that performs no
command claim and no ERP write. Screening results then pass through a second,
independent gate: `app/risk.py` scores the case against a versioned policy
fixture (`policy/supplier_risk.v1.json`) and returns one of three bands.
`clear` queues the ERP command; `review` parks the case as
`awaiting_approval`; `blocked` quarantines it. A supplier yente flags as a
match scores at or above the block threshold and never reaches the command
queue.

Two honest limits. The `review` band is a parked case, not a live pause —
`RequestInput` is not in this graph, and Ground Control reinstates a real
pause on this same branch later. And the score is computed from screening
results and case state only: the Evidence agent is still a stub, so nothing
here is evidence-grounded yet.
```

- [ ] **Step 3: Fix the thin-vertical verify docstring**

In `spikes/thin_vertical/verify.py`, line 30 reads `Screening is recorded but advisory-only in this slice — see`. Replace that sentence with:

```text
Screening was advisory when this spike ran; the risk gate that supersedes it
lands in spikes/policy_gate. This script is preserved as the day-3 artifact
and is not updated to the new behaviour.
```

- [ ] **Step 4: Write the evidence harness**

Create `spikes/policy_gate/verify.py`:

```python
"""Gate evidence: a flagged supplier produces zero ERP writes.

Writes spikes/policy_gate/evidence.json. Evidence belongs in the repo, never
in a scratchpad — a previous gate's proof was lost that way.

Run: uv run python spikes/policy_gate/verify.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.risk import assess_case, load_policy

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
```

- [ ] **Step 5: Run the harness**

Run: `uv run python spikes/policy_gate/verify.py`

Expected: `"result": "PASS"`, `"passed": "4/4"`, and `spikes/policy_gate/evidence.json` written.

- [ ] **Step 6: Update the R30 risk row**

In `strategy/risk-register.md`, the R30 row's mitigation cell currently says the risk is "Mitigated for now by honesty, not by behaviour" and that the real fix "MUST land in days 5–7". Replace that mitigation cell with:

```text
**CLOSED BY BEHAVIOUR 2026-08-14** — the deterministic risk gate (`app/risk.py`
+ `policy/supplier_risk.v1.json`, flight plan step 5) landed a day early. A
confirmed yente match scores 0.70 against a 0.60 block threshold and
terminates at `quarantine_case` with zero outbox writes; a sub-threshold
near-match and an unreachable screening service both park the case as
`awaiting_approval`. The Cloud Run executor independently refuses to drain a
command whose case is not `clear`. Evidence: `spikes/policy_gate/evidence.json`
(4/4). README, `queue_supplier`, and `verify.py` no longer describe screening
as advisory. **Residual:** the sanctioned supplier record from the day-3
evidence run still exists in ERPNext and must be deleted before on-camera
work; the score is not evidence-grounded until the Evidence agent exists.
```

Then run `rumdl fmt strategy/risk-register.md`.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -v && uv run bash scripts/doctor.sh`

Expected: all tests pass; doctor.sh green.

```bash
git add app/nodes.py README.md spikes/thin_vertical/verify.py spikes/policy_gate/
git commit -m "docs: retract the advisory-screening language; the gate now enforces"
git -C ~/dev/git/keplaria-strategy add -A
git -C ~/dev/git/keplaria-strategy commit -m "chore: close R30 — the risk gate enforces by behaviour"
```

---

## Follow-ups this plan deliberately does not do

- Delete the sanctioned supplier record left in ERPNext by the day-3 evidence run.
- Fix `certificate_received` queueing `create_supplier` for an already-known supplier.
- Expiry / hold / release rules (days 5–7 renewal work; they extend this fixture).
- The domain eval cases — contract tests 1, 2 and 4 become their substance, but the eval set is separate work against the overdue day-1 gate.
