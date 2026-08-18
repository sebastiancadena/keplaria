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

The budgets below cap that tail. They are set from observed medians rather than
minimised:

  - coordinator (512): recent draws are 171-414 tokens, so this rarely binds.
    Deliberately generous -- the known failure mode here is a routing miss
    (an empty route failing closed to quarantine, seen 2026-08-15), and the
    coordinator is only ~3s of the run. Cutting time here buys little and
    risks the one thing that must not flake on camera.
  - evidence (1024) and compliance (768): where the time actually is. Both sit
    near their observed medians, so a typical call is unaffected while the
    long tail is truncated.

A budget of 0 would disable thinking entirely and -1 restores the automatic
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
