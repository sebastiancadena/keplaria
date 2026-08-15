"""Deterministic policy and risk scoring.

The compliance-critical decision is code plus versioned data, never model
output. `assess` is a total function: it never raises, because the serving
platform allows only one concurrent query, so a raising gate becomes retry
pressure rather than a decision. Every failure path instead produces a verdict
carrying a reason code, so an auditable outcome always reaches the case
document.

The split that keeps the policy suppliable by a later BigQuery adapter without
an expression language: condition *kinds* are code in CONDITION_KINDS; the
fixture chooses kinds, supplies their parameters, and assigns weights.
"""

from __future__ import annotations

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


class LifecycleTiming(BaseModel):
    """Clock rules for the station-keeping branches.

    Versioned data rather than constants, for the same reason the scoring
    weights are: how long before expiry a renewal may be requested, and how
    long an overdue certificate is tolerated, are compliance decisions.
    """

    renewal_window_days: int = Field(ge=0)
    overdue_grace_days: int = Field(ge=0)


class Factor(BaseModel):
    id: str
    weight: float = Field(ge=0.0, le=1.0)
    description: str = ""
    when: dict


class Policy(BaseModel):
    policy_id: str
    policy_version: int
    thresholds: Thresholds
    lifecycle: LifecycleTiming
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
    scores = {c.get("id"): c.get("score") or 0.0 for c in screening.get("candidates") or []}
    top = flagged[0]
    return True, f"{top} @ {scores.get(top, 0.0):.3f}"


def _screening_candidate_above(params: dict, screening: dict) -> tuple[bool, str]:
    floor = params["score"]
    above = [c for c in screening.get("candidates") or [] if (c.get("score") or 0.0) >= floor]
    if not above:
        return False, ""
    top = max(above, key=lambda c: c.get("score") or 0.0)
    return True, f"{top.get('id')} @ {(top.get('score') or 0.0):.3f}"


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


def lifecycle_timing(path: Path | None = None) -> LifecycleTiming:
    """The loaded policy's timing. Total — never raises.

    Fails closed: with no policy, the renewal window is zero (a renewal is
    never "due early") and the grace period is zero (an overdue certificate
    is never tolerated). Both directions withhold rather than grant.
    """
    try:
        return _cached_policy(path).lifecycle
    except PolicyLoadError:
        return LifecycleTiming(renewal_window_days=0, overdue_grace_days=0)


# --- assessment ------------------------------------------------------------

def _band(policy: Policy, score: float) -> str:
    if score >= policy.thresholds.block:
        return BLOCKED
    if score >= policy.thresholds.review:
        return REVIEW
    return CLEAR


# The complete set of candidate fields anything downstream reads. Every
# condition kind in CONDITION_KINDS, plus app.nodes._record_outcome's
# persisted case summary, reads only this fixed handful of fields off each
# candidate dict — nothing else. _is_malformed below validates exactly this
# set before any of those readers ever see a candidate. If a new condition
# kind or a new persistence path starts reading a new candidate field, add
# that field's validation here too, in the same place — do not validate a
# field only where the first reproduced crash happened to be.
#
#   id    - used as the dict KEY in _screening_match's `{c.get("id"): ...}`
#           comprehension and as the lookup value in
#           _screening_candidate_above; must be a `str` if present. A str is
#           what every consumer actually expects — checking mere
#           hashability is not enough, and hashability is also weaker than
#           what production ever legitimately sends (yente always returns a
#           string entity ID).
#   score - used in numeric `>=` comparisons in _screening_match and
#           _screening_candidate_above; must be `int`/`float` and not `bool`
#           if present (see the comment below).
#   match - read only by app.nodes._record_outcome when persisting the case
#           summary; no condition kind reads it. Because nothing here scores
#           on it, it is not type-validated — but the persistence path must
#           still tolerate any type, or absence, without raising (it uses
#           `.get()`, not `[]`).
def _is_malformed(screening: dict) -> bool:
    if not isinstance(screening.get("reachable"), bool):
        return True

    candidates = screening.get("candidates", [])
    if not isinstance(candidates, list):
        return True
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return True
        if "id" in candidate and not isinstance(candidate["id"], str):
            # Used as a dict key in _screening_match — a list or dict id
            # raises TypeError: unhashable type before the gate can even
            # decide a band. Reject at validation time instead.
            return True
        if "score" in candidate:
            score = candidate["score"]
            # bool is a subclass of int in Python, but a screening score is
            # never legitimately True/False — accepting it would silently
            # coerce to 1.0/0.0 in the >= comparisons below. Reject it.
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                return True

    flagged = screening.get("flagged", [])
    if not isinstance(flagged, list):
        return True
    if not all(isinstance(entry, str) for entry in flagged):
        return True

    return False


def assess(policy: Policy, *, screening: dict | None, case: dict) -> RiskVerdict:
    """Score the case. Total — never raises.

    Precondition: `policy` must come from `load_policy()`, which validates that
    all condition kinds are registered.

    `screening is None` means screening was never required for this event type
    (the `skip` branch). That is deliberately NOT the same as a screening
    attempt that failed, which fires SCREENING_UNAVAILABLE.

    `RiskVerdict.score` is always in `[0, 1]`: it is a sum of factor weights,
    each already constrained to `[0, 1]` by `Factor.weight`, clamped with
    `min(1.0, ...)` below. That clamp is the enforcement point, deliberately
    not a pydantic `Field(ge=0.0, le=1.0)` on `RiskVerdict.score` — a
    constraint there would make `RiskVerdict(...)` construction itself raise
    `ValidationError` on an out-of-range score, which would violate the
    total-function contract this docstring opens with.

    `case` is accepted but not yet read by any condition kind: it is reserved
    for the case-state factors the fixture schema leaves room for (see
    Factor.when), not dead weight to be dropped.
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
