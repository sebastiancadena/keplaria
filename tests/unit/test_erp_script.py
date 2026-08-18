"""The maintenance script's pure logic, with no ERP on the other end.

`scripts/erp.py` talks to a live admin-scoped Frappe site, so everything here
tests the parts that decide WHAT to touch, never the touching. The two things
worth protecting are the reason this widening happened at all: a sanctioned
name can outlive the Supplier that carried it, sitting in a Communication
subject or a File name where the supplier-only audit printed PASS over it; and
Frappe's folder rows live in the same `File` doctype as the certificates, so a
purge that treats File as a flat list can delete the site's directory tree.
"""

from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "erp.py"
_SPEC = importlib.util.spec_from_file_location("erp_maintenance", _PATH)
erp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(erp)

LIVE = {"Andes Verde Import Export SAS", "Talleres Cerro Dorado SAS"}

# One real watchlist shape: a long normalised key, and a short alias that has
# to survive being embedded in a longer subject line.
WATCH = {
    "comercializadoraandesverdesas": ("NR-001", "sanction"),
    "deltasur": ("NR-002", "sanction"),
}


def _comm(**over) -> dict:
    row = {
        "name": "abc123",
        "reference_doctype": "Supplier",
        "reference_name": "Talleres Cerro Dorado SAS",
        "subject": "Certificate renewal required",
    }
    row.update(over)
    return row


def _file(**over) -> dict:
    row = {
        "name": "3b30ce0937",
        "attached_to_doctype": "Supplier",
        "attached_to_name": "Andes Verde Import Export SAS",
        "file_name": "Andes Verde Import Export SAS-cert-c1.pdf",
        "is_folder": 0,
    }
    row.update(over)
    return row


def test_a_row_pointing_at_a_live_supplier_is_linked():
    assert erp.link_state(_comm(), "Communication", LIVE) == erp.LINKED


def test_a_row_pointing_at_a_deleted_supplier_is_orphaned():
    """The walkthrough's supplier was removed; its Communication was not."""
    row = _comm(reference_name="Empaques Sabana Norte SAS")
    assert erp.link_state(row, "Communication", LIVE) == erp.ORPHANED


def test_a_row_carrying_no_reference_at_all_is_unlinked():
    row = _comm(reference_doctype=None, reference_name=None)
    assert erp.link_state(row, "Communication", LIVE) == erp.UNLINKED


def test_a_file_is_classified_through_its_own_field_names():
    """File spells the link `attached_to_*`; Communication spells it `reference_*`."""
    assert erp.link_state(_file(), "File", LIVE) == erp.LINKED
    gone = _file(attached_to_name="Empaques Sabana Norte SAS")
    assert erp.link_state(gone, "File", LIVE) == erp.ORPHANED


def test_a_row_pointing_at_some_other_doctype_is_not_a_supplier_orphan():
    """A Communication about a Purchase Order is simply not this audit's business."""
    row = _comm(reference_doctype="Purchase Order", reference_name="PO-0001")
    assert erp.link_state(row, "Communication", LIVE) == erp.LINKED


def test_a_sanctioned_name_in_a_reference_that_outlived_its_supplier_fails():
    """The gap this widening closes.

    Deleting a Supplier does not take its correspondence with it, so the
    sanctioned name survives in a row the supplier-only audit never read.
    Compared whole, the way a supplier name is compared.
    """
    row = _comm(reference_name="Comercializadora Andes Verde S.A.S.", subject="Hold")
    findings = erp.row_findings(row, "Communication", WATCH)
    assert len(findings) == 1
    assert "NR-001" in findings[0]


def test_a_certificate_filed_under_a_sanctioned_entity_fails():
    row = _file(attached_to_name="Comercializadora Andes Verde SAS")
    findings = erp.row_findings(row, "File", WATCH)
    assert len(findings) == 1
    assert "NR-001" in findings[0]


def test_a_near_miss_supplier_name_must_not_fail_the_audit():
    """`Andes Verde Import Export SAS` is a legitimate demo supplier.

    The watchlist alias `Andes Verde` is a PREFIX of it on purpose: that is
    the review-band case the demo exists to show, approved by a human and
    rightly present in the ERP. If a substring rule governed the exit code,
    every certificate that supplier owns would fail the audit on every run,
    and the check would stop being read.
    """
    watch = dict(WATCH, andesverde=("NR-001", "sanction"))
    assert erp.row_findings(_file(), "File", watch) == []
    assert erp.row_findings(_comm(), "Communication", watch) == []


def test_a_sanctioned_name_inside_a_longer_subject_is_a_warning():
    """'Delta Sur' only appears as a substring of the normalised subject.

    Equality — right for a supplier name — reads straight past it, so free
    text is scanned by substring. It warns rather than fails, because the
    same rule cannot separate a real mention from the near-miss above.
    """
    row = _comm(subject="Re: Delta Sur Shipping - certificate query")
    mentions = erp.row_mentions(row, "Communication", WATCH)
    assert len(mentions) == 1
    assert "NR-002" in mentions[0]


def test_ordinary_rows_produce_neither_findings_nor_mentions():
    for row, doctype in ((_comm(), "Communication"), (_file(), "File")):
        assert erp.row_findings(row, doctype, WATCH) == []
        assert erp.row_mentions(row, doctype, WATCH) == []


def test_frappe_folder_rows_are_protected():
    rows = [
        {"name": "Home", "is_folder": 1},
        {"name": "Home/Attachments", "is_folder": 1},
        _file(),
    ]
    assert erp.protected_files(rows) == ["Home", "Home/Attachments"]


def test_purge_refuses_a_folder_even_when_it_is_named_explicitly(monkeypatch):
    """Naming the target is normally enough. For the folder tree it is not."""
    monkeypatch.setattr(erp, "_files_by_name", lambda names: [{"name": "Home", "is_folder": 1}])
    args = Namespace(
        test_suppliers=False, supplier=None, case=None,
        communication=None, file=["Home"], yes=True, database="(default)",
    )
    assert erp.cmd_purge(args) == 2


def test_purge_with_no_target_still_refuses():
    args = Namespace(
        test_suppliers=False, supplier=None, case=None,
        communication=None, file=None, yes=True, database="(default)",
    )
    assert erp.cmd_purge(args) == 2
