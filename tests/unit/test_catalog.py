"""The fleet catalog loader: all structural validation at load time.

A catalog that cannot load refuses everything downstream (fail closed); a
catalog that loads is structurally impossible to misread at decision time.
Same argument, and same shape, as app.risk.load_policy.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import app.catalog as catalog_module
from app.catalog import CatalogLoadError, load_catalog


def _catalog_dict() -> dict:
    """A minimal valid catalog, mirroring catalog/fleet.v1.json's shape.

    Tests mutate deep copies of this; the committed artifact has its own
    dedicated test below so the two cannot drift apart silently.
    """
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
                "permitted_commands": [
                    "create_supplier", "attach_evidence",
                    "request_renewal", "apply_hold", "clear_hold",
                ],
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


def _write(tmp_path: Path, catalog: dict) -> Path:
    path = tmp_path / "fleet.test.json"
    path.write_text(json.dumps(catalog))
    return path


def test_the_committed_catalog_loads_and_validates():
    catalog = load_catalog()
    assert catalog.catalog_id == "fleet"
    assert catalog.catalog_version == 1
    assert catalog.routable_ids() == ("evidence", "compliance")
    assert set(catalog.departments) == {"procurement", "compliance", "finance"}


def test_corrupt_json_refuses_to_load(tmp_path):
    path = tmp_path / "fleet.test.json"
    path.write_text("{not json")
    with pytest.raises(CatalogLoadError, match="cannot load catalog"):
        load_catalog(path)


def test_a_route_naming_an_undeclared_agent_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["event_routes"]["certificate_received"] = ["evidence", "phantom"]
    with pytest.raises(CatalogLoadError, match="not a declared routable agent"):
        load_catalog(_write(tmp_path, catalog))


def test_a_route_naming_a_non_routable_agent_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["event_routes"]["certificate_received"] = ["coordinator"]
    with pytest.raises(CatalogLoadError, match="not a declared routable agent"):
        load_catalog(_write(tmp_path, catalog))


def test_a_department_naming_an_unknown_agent_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["departments"]["finance"]["permitted_agents"] = ["phantom"]
    with pytest.raises(CatalogLoadError, match="permits unknown agent"):
        load_catalog(_write(tmp_path, catalog))


def test_a_department_naming_an_unknown_command_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["departments"]["finance"]["permitted_commands"] = ["submit_po"]
    with pytest.raises(CatalogLoadError, match="permits unknown command"):
        load_catalog(_write(tmp_path, catalog))


def test_a_clock_event_with_a_non_empty_route_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["event_routes"]["renewal_due"] = ["evidence"]
    with pytest.raises(CatalogLoadError, match="must map to an empty route"):
        load_catalog(_write(tmp_path, catalog))


def test_compliance_ordered_before_evidence_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    agents = catalog["agents"]
    agents[1], agents[2] = agents[2], agents[1]  # swap evidence/compliance
    with pytest.raises(CatalogLoadError, match="evidence must precede compliance"):
        load_catalog(_write(tmp_path, catalog))


def test_an_unknown_key_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["departments"]["finance"]["discoverable_agents"] = []
    with pytest.raises(CatalogLoadError, match="cannot load catalog"):
        load_catalog(_write(tmp_path, catalog))


def test_a_null_legacy_department_is_valid(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["legacy"]["v1_department"] = None
    loaded = load_catalog(_write(tmp_path, catalog))
    assert loaded.legacy.v1_department is None


def test_a_legacy_department_not_declared_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["legacy"]["v1_department"] = "warehouse"
    with pytest.raises(CatalogLoadError, match="not a declared department"):
        load_catalog(_write(tmp_path, catalog))


def test_duplicate_agent_ids_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["agents"][2]["id"] = "evidence"  # compliance now collides with evidence
    with pytest.raises(CatalogLoadError, match="duplicate agent ids"):
        load_catalog(_write(tmp_path, catalog))


def test_an_unknown_deployment_state_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    catalog["agents"][1]["deployment"] = "active"
    with pytest.raises(CatalogLoadError, match="unknown deployment state"):
        load_catalog(_write(tmp_path, catalog))


def test_a_clock_event_missing_from_event_routes_refuses_to_load(tmp_path):
    catalog = copy.deepcopy(_catalog_dict())
    del catalog["event_routes"]["evidence_overdue"]
    with pytest.raises(CatalogLoadError, match="missing from event_routes"):
        load_catalog(_write(tmp_path, catalog))


@pytest.fixture
def install_catalog(tmp_path, monkeypatch):
    """Point the default loader path at a temp catalog and reset the cache.

    get_catalog/load_catalog read DEFAULT_CATALOG_PATH at call time, so
    monkeypatching the module attribute redirects every consumer — policy,
    nodes, console — through the same loader. That sameness is the point.
    """
    def _install(catalog: dict | None, *, missing: bool = False) -> Path:
        path = tmp_path / "fleet.test.json"
        if not missing:
            path.write_text(json.dumps(catalog))
        monkeypatch.setattr(catalog_module, "DEFAULT_CATALOG_PATH", path)
        catalog_module.reset_catalog_cache()
        return path
    yield _install
    catalog_module.reset_catalog_cache()


def test_canonical_order_follows_catalog_agent_order(install_catalog):
    """The returned route order is the catalog's declaration order, not an
    alphabetical sort: `evidence` is declared before `compliance`, but
    `compliance` sorts before `evidence` alphabetically, so this would catch
    `routable_ids()` being reimplemented to sort its result alphabetically."""
    from app.catalog import get_catalog
    install_catalog(_catalog_dict())
    assert get_catalog().routable_ids() == ("evidence", "compliance")


def test_catalog_load_failure_fails_closed_in_validate_route(install_catalog):
    """THE fail-closed test. If any fallback map is ever added to the
    load-failure path, the route below succeeds and this test goes red —
    which is exactly what it exists to catch."""
    from app.policy import PolicyError, validate_route
    install_catalog(None, missing=True)
    with pytest.raises(PolicyError, match="CATALOG_UNAVAILABLE"):
        validate_route("new_supplier_packet", ["evidence"], "procurement")
