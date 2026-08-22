"""The catalog cannot invent runnable agents, and manifests cannot lie
about tools. The graph's nodes and edges are code; this file pins the
artifact to that code so the two cannot drift apart silently."""

from __future__ import annotations

from app.agent import compliance_agent, coordinator, evidence_agent
from app.catalog import get_catalog


def test_catalog_routable_ids_match_the_graph_branch_vocabulary():
    """apply_route branches on exactly these two names; a catalog declaring
    a routable agent the graph lacks would authorize something that cannot
    run, and a catalog missing one would refuse routes the graph wires."""
    assert set(get_catalog().routable_ids()) == {"evidence", "compliance"}


def test_manifest_approved_tools_match_the_live_agents():
    """The fleet view renders approved_tools as fact; this is what makes
    it one. Adding a tool to an agent without amending its manifest — or
    padding a manifest with a tool the agent does not hold — goes red."""
    catalog = get_catalog()
    live = {
        "coordinator": coordinator,
        "evidence": evidence_agent,
        "compliance": compliance_agent,
    }
    for manifest in catalog.agents:
        agent = live[manifest.id]
        live_tools = sorted(
            getattr(tool, "__name__", None) or getattr(tool, "name", str(tool))
            for tool in (agent.tools or [])
        )
        assert live_tools == sorted(manifest.approved_tools), (
            f"manifest {manifest.id} declares {manifest.approved_tools}, "
            f"live agent holds {live_tools}"
        )
