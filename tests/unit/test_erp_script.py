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
        communication=None, contact=None, file=["Home"], yes=True, database="(default)",
    )
    assert erp.cmd_purge(args) == 2


def test_purge_with_no_target_still_refuses():
    args = Namespace(
        test_suppliers=False, supplier=None, case=None,
        communication=None, contact=None, file=None, yes=True, database="(default)",
    )
    assert erp.cmd_purge(args) == 2


def test_a_target_named_by_a_spike_evidence_file_is_cited(tmp_path, monkeypatch):
    """Hermetic: a temporary spikes tree, so the assertion is about the rule
    and not about whichever evidence happens to be committed today."""
    (tmp_path / "judge_run").mkdir()
    (tmp_path / "judge_run" / "evidence.json").write_text(
        '{"suppliers": ["Andes Verde Import Export SAS"], "case_ids": ["JR-A-1C1535"]}'
    )
    monkeypatch.setattr(erp, "SPIKES", tmp_path)

    cited = erp.cited_by_evidence(
        ["Andes Verde Import Export SAS", "JR-A-1C1535", "TEST Supplier 4"]
    )

    assert set(cited) == {"Andes Verde Import Export SAS", "JR-A-1C1535"}
    # Reported relative to the repo root in production ("spikes/<name>/..."),
    # which under a tmp_path SPIKES becomes the tmp dir's own name.
    assert cited["JR-A-1C1535"][0].endswith("judge_run/evidence.json")


def test_purge_refuses_a_target_a_spike_evidence_file_cites(tmp_path, monkeypatch):
    """The rule that did not exist on day 7.

    A cleanup deleted the case and supplier that `one_erp_write_after_retry`
    reads, and the criterion could not be re-proven until the drill was run
    again. Naming the target is not enough when a committed proof asserts
    something about it, so the refusal happens before the dry-run print — a
    purge that would destroy evidence cannot be rehearsed and then confirmed.
    """
    (tmp_path / "core_contracts").mkdir()
    (tmp_path / "core_contracts" / "retry_drill.json").write_text(
        '{"case_id": "DLQ-SWEEP-C1BDE3FC", "supplier": "DLQ Sweep Probe SAS"}'
    )
    monkeypatch.setattr(erp, "SPIKES", tmp_path)
    args = Namespace(
        test_suppliers=False, supplier=["DLQ Sweep Probe SAS"],
        case=None, communication=None, contact=None, file=None, yes=True, database="(default)",
    )

    assert erp.cmd_purge(args) == 2


def test_purge_override_for_a_record_the_next_run_recreates_prints_the_citations(
    tmp_path, monkeypatch, capsys
):
    """The live take needs the demo supplier absent so its approval is a real
    create, and the take itself recreates it minutes later. The override is
    explicit, prints what it is overriding, and still stops at the dry run
    without --yes; a Namespace without the flag behaves as before."""
    (tmp_path / "judge_run").mkdir()
    (tmp_path / "judge_run" / "evidence.json").write_text(
        '{"suppliers": {"hitl": "Andes Verde Import Export SAS"}}'
    )
    monkeypatch.setattr(erp, "SPIKES", tmp_path)
    args = Namespace(
        test_suppliers=False, supplier=["Andes Verde Import Export SAS"],
        case=None, communication=None, contact=None, file=None, yes=False,
        database="(default)", recreated_by_next_run=True,
    )

    assert erp.cmd_purge(args) == 0
    out = capsys.readouterr().out
    assert "deleting anyway" in out
    assert "judge_run/evidence.json" in out
    assert "Dry run" in out


def test_purge_still_proceeds_for_a_target_no_evidence_mentions(tmp_path, monkeypatch, capsys):
    """The guard must not become a refusal of everything: residue is exactly
    what purge exists to remove, and a rule that blocks it would be worked
    around rather than obeyed."""
    (tmp_path / "core_contracts").mkdir()
    (tmp_path / "core_contracts" / "retry_drill.json").write_text('{"supplier": "DLQ Sweep Probe SAS"}')
    monkeypatch.setattr(erp, "SPIKES", tmp_path)
    args = Namespace(
        test_suppliers=False, supplier=["TEST Supplier 12"],
        case=None, communication=None, contact=None, file=None, yes=False, database="(default)",
    )

    assert erp.cmd_purge(args) == 0
    assert "TEST Supplier 12" in capsys.readouterr().out


def _contact(**over) -> dict:
    """A Contact row as `_rows` returns it, with its links already resolved.

    Contact does not carry a flat supplier field the way Communication and
    File do. Its link lives in a `Dynamic Link` child table, which Frappe
    refuses to serve as a list query (403 even for the site owner), so the
    reader fetches each Contact document and flattens the first Supplier
    link it finds into the same two field names the other doctypes use.
    """
    row = {
        "name": "Andes Verde Import Export SAS-Andes Verde Import Export SAS",
        "link_doctype": "Supplier",
        "link_name": "Andes Verde Import Export SAS",
        "company_name": "Andes Verde Import Export SAS",
        "first_name": None,
        "email_id": "andes-verde-import-export-sas@example.com",
    }
    row.update(over)
    return row


def test_a_contact_pointing_at_a_live_supplier_is_linked():
    assert erp.link_state(_contact(), "Contact", LIVE) == erp.LINKED


def test_a_contact_whose_supplier_was_purged_is_orphaned():
    """The gap that made this widening necessary.

    Purging a Supplier does not take its Contact with it, and the Contact's
    name is BUILT from the supplier name — so a sanctioned name outlives the
    record that carried it in a row the audit could not see at all.
    """
    row = _contact(
        name="Empaques Sabana Norte SAS-Empaques Sabana Norte SAS",
        link_name="Empaques Sabana Norte SAS",
    )
    assert erp.link_state(row, "Contact", LIVE) == erp.ORPHANED


def test_a_person_contact_with_no_supplier_link_is_unlinked():
    """The site owner and the bot users each have a Contact. Not this audit's business."""
    row = _contact(name="Spike Bot", link_doctype=None, link_name=None,
                   company_name=None, first_name="Spike Bot")
    assert erp.link_state(row, "Contact", LIVE) == erp.UNLINKED


def test_a_contact_filed_under_a_sanctioned_supplier_fails_the_audit():
    row = _contact(
        name="Comercializadora Andes Verde SAS-Comercializadora Andes Verde SAS",
        link_name="Comercializadora Andes Verde SAS",
    )
    findings = erp.row_findings(row, "Contact", WATCH)
    assert findings and "NR-001" in findings[0]


def test_a_contact_naming_a_sanctioned_entity_in_its_text_only_warns():
    """Same rule as Communication and File: substring cannot tell a near miss apart.

    The Contact NAME is scanned, not only its fields, because that is where
    the supplier name actually lands — Frappe builds it as `<supplier>-<supplier>`.
    """
    row = _contact(name="Deltasur Holdings-Deltasur Holdings",
                   link_doctype=None, link_name=None, company_name=None)
    assert erp.row_findings(row, "Contact", WATCH) == []
    mentions = erp.row_mentions(row, "Contact", WATCH)
    assert mentions and "NR-002" in mentions[0]


def test_the_legitimate_supplier_contact_neither_fails_nor_warns():
    """`Andes Verde Import Export SAS` is the real supplier and must stay auditable.

    It is filed under itself, which is not a watchlist name, and it embeds no
    watchlist key -- so it produces nothing at all. That is the outcome that
    matters: a check which flagged this row every run would stop being read,
    which is the reasoning already written into `row_mentions`.
    """
    row = _contact()
    assert erp.row_findings(row, "Contact", WATCH) == []
    assert erp.row_mentions(row, "Contact", WATCH) == []


def test_a_contact_embedding_an_alias_warns_but_does_not_fail():
    """The near-miss rule, on the field where a supplier name actually lands.

    A Contact named for a supplier whose name merely CONTAINS a watchlist
    alias is not evidence of a sanctioned counterparty, and substring cannot
    tell the two apart -- so it warns and stays out of the exit code, exactly
    as a Communication subject does.
    """
    row = _contact(name="Deltasur Andina SAS-Deltasur Andina SAS",
                   link_name="Deltasur Andina SAS", company_name="Deltasur Andina SAS")
    assert erp.row_findings(row, "Contact", WATCH) == []
    mentions = erp.row_mentions(row, "Contact", WATCH)
    assert mentions and "NR-002" in mentions[0]


def test_one_row_naming_one_entity_warns_once_however_many_fields_carry_it():
    """A Contact repeats the supplier name in three fields. That is one finding.

    Reported per field, the pre-recording WARN block showed a single
    legitimate near-miss row as three separate lines. The block is meant to be
    read by a human before a recording, and inflating one row into three is
    the same failure mode the near-miss rule already guards against.
    """
    row = _contact(name="Deltasur Andina SAS-Deltasur Andina SAS",
                   company_name="Deltasur Andina SAS",
                   email_id="deltasur-andina@example.com",
                   link_doctype=None, link_name=None)
    mentions = erp.row_mentions(row, "Contact", WATCH)
    assert len(mentions) == 1, mentions
    assert "NR-002" in mentions[0]
    assert "name" in mentions[0] and "company_name" in mentions[0]


def test_two_different_entities_in_one_row_are_still_two_findings():
    """Collapsing is per entity, not per row: two names is two things to read."""
    row = _contact(name="Deltasur Comercializadora Andes Verde SAS",
                   company_name=None, email_id=None,
                   link_doctype=None, link_name=None)
    mentions = erp.row_mentions(row, "Contact", WATCH)
    assert len(mentions) == 2, mentions
    assert {"NR-001", "NR-002"} == {m.split("names ")[1].split(" ")[0] for m in mentions}
