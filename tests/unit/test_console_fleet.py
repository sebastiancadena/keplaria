"""The fleet view renders the SAME artifact the authorization path loads.

The sentinel test is what makes this view non-presentational: a
hand-maintained template copy of the fleet cannot show an agent id that
exists only in the injected catalog.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

import app.catalog as catalog_module
from console.public import api


def _catalog_dict() -> dict:
    """A minimal valid catalog. Same shape as catalog/fleet.v1.json; kept
    locally so this file reads standalone."""
    evals = {
        "suite": "tests/eval/run_domain_evals.sh",
        "metric": "domain_case_pass",
        "evidence": "spikes/domain_evals/evidence.json",
    }

    def agent(id_: str, routable: bool) -> dict:
        return {
            "id": id_, "routable": routable, "version": 1,
            "owner": "workflow-platform", "purpose": f"{id_} purpose.",
            "input_schema": "app.schemas:CanonicalEvent",
            "output_schema": "app.schemas:RoutingDecision",
            "approved_tools": [], "data_classes": ["entity_identifiers"],
            "deployment": "deployed", "evals": evals,
        }

    return {
        "catalog_id": "fleet",
        "catalog_version": 1,
        "agents": [
            agent("coordinator", False),
            agent("evidence", True),
            agent("compliance", True),
        ],
        "event_routes": {
            "new_supplier_packet": ["evidence", "compliance"],
            "certificate_received": ["evidence"],
            "renewal_due": [],
            "evidence_overdue": [],
        },
        "departments": {
            "procurement": {
                "description": "Onboards and maintains suppliers.",
                "permitted_agents": ["evidence", "compliance"],
                "permitted_commands": ["create_supplier", "attach_evidence",
                                       "request_renewal", "apply_hold",
                                       "clear_hold"],
            },
            "compliance": {
                "description": "Reviews screening outcomes.",
                "permitted_agents": ["evidence", "compliance"],
                "permitted_commands": ["apply_hold", "clear_hold"],
            },
            "finance": {
                "description": "Consumes approved supplier records.",
                "permitted_agents": [],
                "permitted_commands": [],
            },
        },
        "legacy": {"v1_department": "procurement"},
    }


@pytest.fixture
def client():
    return TestClient(api)


@pytest.fixture
def install_catalog(tmp_path, monkeypatch):
    def _install(catalog: dict | None, *, missing: bool = False):
        path = tmp_path / "fleet.test.json"
        if not missing:
            path.write_text(json.dumps(catalog))
        monkeypatch.setattr(catalog_module, "DEFAULT_CATALOG_PATH", path)
        catalog_module.reset_catalog_cache()
    yield _install
    catalog_module.reset_catalog_cache()


def test_the_fleet_view_renders_department_scopes_from_the_catalog(
    client, install_catalog
):
    """Sentinel agent id, present only in the injected catalog: the page
    can only show it by reading the same loader authorization reads."""
    catalog = copy.deepcopy(_catalog_dict())
    sentinel = copy.deepcopy(catalog["agents"][2])
    sentinel["id"] = "sentinel-probe-9"
    catalog["agents"].append(sentinel)
    catalog["departments"]["procurement"]["permitted_agents"].append(
        "sentinel-probe-9"
    )
    install_catalog(catalog)

    response = client.get("/fleet")

    assert response.status_code == 200
    assert "sentinel-probe-9" in response.text
    assert "finance" in response.text
    assert "refused and recorded" in response.text


def test_the_fleet_view_renders_the_coordinator_fleet_wide(
    client, install_catalog
):
    """The coordinator is not an authorization target — it belongs to no
    department scope, so it renders in the fleet-wide section rather than
    inside any department card."""
    install_catalog(_catalog_dict())

    response = client.get("/fleet")

    assert response.status_code == 200
    assert "coordinator" in response.text
    assert "Fleet-wide" in response.text


def test_an_unavailable_catalog_renders_an_explicit_error(
    client, install_catalog
):
    """Never an empty fleet — an empty page would read as 'no agents
    exist'. The same load failure refuses routing, and the page says so."""
    install_catalog(None, missing=True)

    response = client.get("/fleet")

    assert response.status_code == 503
    assert "catalog unavailable" in response.text.lower()
