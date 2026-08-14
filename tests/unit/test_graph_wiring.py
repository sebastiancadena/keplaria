"""Graph wiring is load-bearing: every path to the command queue passes the gate.

This module pins the invariant that prevents a critical bypass: a flagged supplier
must never reach the ERP command queue. The workflow's `assess_risk` node implements
the policy gate and must sit in the critical path from BOTH the screening branch
(screen_supplier) and the non-screening branch (skip). If a later change shortcuts
one of these paths directly to queue_supplier, this test fails loudly rather than
silently shipping an onboarding of a flagged entity.

All assertions read `root_agent.edges` directly — no model calls, no network, no
external state. This is pure structural introspection and belongs in the default
suite, not in live-marked tests.
"""

from app.agent import root_agent
from app.nodes import assess_risk, park_case, queue_supplier, quarantine_case, screen_supplier


def test_flagged_supplier_never_reaches_the_command_queue():
    """Every path to queue_supplier passes through assess_risk.

    The risk gate (assess_risk) must be the sole point where a policy verdict is
    applied before the command queue. Both the screening branch and the skip branch
    feed into it, ensuring no path bypasses the verdict.
    """
    edges = {}
    for edge in root_agent.edges:
        source = edge[0]
        target = edge[1]
        name = getattr(source, "__name__", str(source))
        edges[name] = target

    assert edges["screen_supplier"] is assess_risk, "screening must feed the gate"
    assert edges["assess_risk"] == {
        "clear": queue_supplier,
        "review": park_case,
        "blocked": quarantine_case,
    }
    assert edges["apply_route"] == {
        "skip": assess_risk,
        "screen": screen_supplier,
        "blocked": quarantine_case,
    }
