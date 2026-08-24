"""Counts are computed from case documents, outbox rows, and inbox rows, no
Firestore. Event types live in each case's inbox subcollection, never on
the case document (see console.store.load_inbox), so exercise_counts takes
events_by_case rather than reading case["event_type"]."""

from __future__ import annotations

from app.catalog import Catalog  # the loader's model; see app/catalog.py
from console.fleet_counts import exercise_counts

# Import the fixture dict from the fleet-view tests so the two files agree.
from tests.unit.test_console_fleet import _catalog_dict


def _catalog() -> Catalog:
    return Catalog.model_validate(_catalog_dict())


def _case(cid, dept, route, refused=()):
    return {
        "case_id": cid,
        "routing": {"department": dept, "route": list(route)},
        "refused_commands": [{"action": a, "cycle": 1} for a in refused],
    }


def test_agent_cells_count_cases_whose_engaged_route_includes_the_agent():
    counts = exercise_counts(_catalog(), [
        _case("A", "procurement", ["evidence", "compliance"]),
        _case("B", "procurement", ["evidence"]),
        _case("C", "compliance", ["compliance"]),
    ], {}, {})
    assert counts["population"] == 3
    assert counts["departments"]["procurement"]["agents"] == {"evidence": 2, "compliance": 1}
    assert counts["departments"]["compliance"]["agents"] == {"evidence": 0, "compliance": 1}
    assert counts["departments"]["finance"]["agents"] == {"evidence": 0, "compliance": 0}


def test_command_cells_count_claimed_outbox_rows_and_refusals_separately():
    counts = exercise_counts(_catalog(), [
        _case("A", "procurement", ["evidence"]),
        _case("F", "finance", [], refused=("create_supplier", "apply_hold")),
    ], {
        "A": [{"action": "create_supplier"}, {"action": "attach_evidence"}],
    }, {})
    proc = counts["departments"]["procurement"]["commands"]
    assert proc["create_supplier"] == {"claimed": 1, "refused": 0}
    assert proc["request_renewal"] == {"claimed": 0, "refused": 0}
    fin = counts["departments"]["finance"]["commands"]
    assert fin["create_supplier"] == {"claimed": 0, "refused": 1}
    assert fin["apply_hold"] == {"claimed": 0, "refused": 1}


def test_event_rows_count_cases_by_event_type():
    counts = exercise_counts(_catalog(), [
        _case("A", "procurement", ["evidence", "compliance"]),
        _case("B", "procurement", []),
        _case("C", "procurement", []),
    ], {}, {
        "A": [{"event_type": "new_supplier_packet", "case_version": 1}],
        "B": [{"event_type": "renewal_due", "case_version": 1}],
        "C": [
            {"event_type": "new_supplier_packet", "case_version": 1},
            {"event_type": "renewal_due", "case_version": 2},
        ],
    })
    assert counts["events"] == {
        "new_supplier_packet": 2, "certificate_received": 0,
        "renewal_due": 2, "evidence_overdue": 0,
    }


def test_a_case_with_a_department_outside_the_catalog_is_counted_in_population_only():
    counts = exercise_counts(_catalog(), [_case("Z", "dept-sentinel-7", ["evidence"])], {}, {})
    assert counts["population"] == 1
    assert counts["departments"]["procurement"]["agents"]["evidence"] == 0
