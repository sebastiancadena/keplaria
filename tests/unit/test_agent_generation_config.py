"""Every LLM agent pins a thinking budget, because latency is a demo contract.

Machine time for the judge run is budgeted, and the budget is spent almost
entirely inside `generate_content`. With no thinking budget set, the model
chooses its own reasoning length for an unchanged prompt: traces from
2026-08-17 and 2026-08-18 show `evidence_agent` emitting between 1169 and 1845
reasoning tokens for byte-identical input, and per-call latency tracks that
count almost linearly. Two runs of the same sequence therefore came in at 74.8s
and 85.4s with no code change between them, and the slowest single call in the
window landed on the *faster* run -- the distribution has a heavy tail, and an
unlucky draw on camera would approach the budget ceiling.

These numbers were chosen as caps near each agent's observed median, on the
assumption that a budget truncates the model's usual reasoning. **Measuring the
deployed engine on 2026-08-18 showed that is not what happens.** The limit
comes back in the trace as a ceiling
(`gen_ai.usage.experimental.reasoning_tokens_limit` = exactly these values)
that the model then leaves nearly empty: the extractor fell from ~1500
reasoning tokens per call to effectively none, and the whole timed sequence
from 85.4s to 56.9s -- far more than capping a tail could explain. So naming
any budget here reads as "do not reason at length", and the specific value
mostly does not matter. Raising one will not buy back a middle setting.

What that bought, and what it cost, both measured rather than assumed: 8/8
graded domain cases before and after, and a full deployed run that parked a
near-match for review, released it on approval, and drove the renewal, hold
and release cycle with every band correct. What it did not buy is a margin
anyone has measured on cases harder than those. The coordinator is the first
place to restore reasoning if a routing flake reappears, because a missed
route is the failure mode that matters there and it costs the least time.

A budget of 0 would disable thinking explicitly and -1 restores the automatic
behaviour this module exists to prevent; both are rejected below.
"""

from google.genai import types

from app.agent import compliance_agent, coordinator, evidence_agent

EXPECTED_BUDGETS = {
    "mission_coordinator": 512,
    "evidence_agent": 1024,
    "compliance_agent": 768,
}


def _agents():
    return [coordinator, evidence_agent, compliance_agent]


def test_every_agent_pins_a_thinking_budget():
    """No agent may fall back to automatic reasoning length."""
    for agent in _agents():
        config = agent.generate_content_config
        assert config is not None, f"{agent.name} has no generate_content_config"
        thinking = config.thinking_config
        assert thinking is not None, (
            f"{agent.name} has no thinking_config: its reasoning length, and so "
            "its share of the run's machine-time budget, is the model's choice"
        )
        assert isinstance(thinking, types.ThinkingConfig)
        budget = thinking.thinking_budget
        assert isinstance(budget, int) and budget > 0, (
            f"{agent.name} thinking_budget is {budget!r}; 0 disables reasoning "
            "outright and -1 is the automatic behaviour this pin exists to stop"
        )


def test_thinking_budgets_are_the_reviewed_values():
    """The budgets were chosen against measured traces; changing one is a decision.

    This is a change-detector on purpose, in the same spirit as the graph-wiring
    pins: the numbers encode a latency/quality trade-off that was validated
    against the domain evals, so a silent edit must fail here rather than
    surface as a slow or degraded run.
    """
    actual = {
        agent.name: agent.generate_content_config.thinking_config.thinking_budget
        for agent in _agents()
    }
    assert actual == EXPECTED_BUDGETS


def test_temperature_stays_pinned_at_zero():
    """The reproducibility pin must survive the addition of the thinking pin."""
    for agent in _agents():
        assert agent.generate_content_config.temperature == 0.0, (
            f"{agent.name} lost temperature=0.0"
        )
