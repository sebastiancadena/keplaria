#!/usr/bin/env python3
"""Maintenance for the demo ERP and the case records that mirror it.

Read-only by default. Every destructive action needs an explicit target AND
`--yes`; there is no "delete everything" verb, on purpose.

    uv run --env-file .env python scripts/erp.py suppliers
    uv run --env-file .env python scripts/erp.py cases
    uv run --env-file .env python scripts/erp.py links
    uv run --env-file .env python scripts/erp.py audit
    uv run --env-file .env python scripts/erp.py purge --test-suppliers --yes
    uv run --env-file .env python scripts/erp.py purge --supplier "NAME" --yes
    uv run --env-file .env python scripts/erp.py purge --case TV-XXXX --yes
    uv run --env-file .env python scripts/erp.py purge --communication ID --file ID --yes

Why this exists rather than an ERP MCP server: these operations are
human-triggered maintenance, not something an agent should be able to reach
for mid-task. The Frappe credentials are admin-scoped, so an always-available
tool would give every agent turn write access to the live ERP. A script keeps
deletion an intentional act.

`audit` answers the question that matters before any recording: is anything
in the ERP or the live case store a sanctions match? It exists because an
exact string search does NOT answer that — the watchlist carries
"Comercializadora Andes Verde S.A.S." while the ERP record read
"Comercializadora Andes Verde SAS", and a grep called that clean. Screening
is fuzzy for exactly this reason. The check here normalises punctuation and
casing before comparing, and it is deliberately coarse: it is a pre-flight
smoke test, not a replacement for yente.

It reads Communication and File rows as well as Suppliers, because deleting a
Supplier leaves both behind: a sanctioned name can outlive the record that
carried it, and a still-linked row can make the ERP refuse the Supplier delete
in the first place. Only the supplier a row is FILED UNDER affects the exit
code. Names spotted inside free text warn instead, since that match is a
substring and cannot tell a real mention from the demo's deliberate near-miss
supplier name.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.executor.frappe import frappe_client  # noqa: E402
from app.state.firestore import CASES, get_client  # noqa: E402

WATCHLIST = Path(__file__).resolve().parent.parent / "fixtures" / "watchlist" / "entities.ftm.json"
SPIKES = Path(__file__).resolve().parent.parent / "spikes"
LIVE_DB = "(default)"


def _norm(name: str) -> str:
    """Casefold, strip accents and every non-alphanumeric character.

    'Comercializadora Andes Verde S.A.S.' and 'Comercializadora Andes Verde SAS'
    both collapse to the same key. That collision is the whole point.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", stripped.casefold())


def _watchlist() -> dict[str, tuple[str, str]]:
    """Map normalised name/alias -> (entity id, topics). Empty if absent."""
    if not WATCHLIST.exists():
        return {}
    index: dict[str, tuple[str, str]] = {}
    for line in WATCHLIST.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        entity = json.loads(line)
        props = entity.get("properties", {})
        topics = ",".join(props.get("topics", []))
        for field in ("name", "alias"):
            for value in props.get(field, []):
                index[_norm(value)] = (entity.get("id", "?"), topics)
    return index


def _suppliers() -> list[dict]:
    with frappe_client() as client:
        response = client.get(
            "/api/resource/Supplier",
            params={
                "fields": '["name","creation"]',
                "order_by": "creation asc",
                "limit_page_length": 500,
            },
        )
        response.raise_for_status()
        return response.json()["data"]


LINKED, ORPHANED, UNLINKED = "linked", "orphaned", "unlinked"

# Communication and File both point at a Supplier, and spell it differently.
LINK_FIELDS = {
    "Communication": ("reference_doctype", "reference_name"),
    "File": ("attached_to_doctype", "attached_to_name"),
}
# Free text each row carries, scanned by substring rather than equality.
TEXT_FIELDS = {"Communication": ("subject",), "File": ("file_name",)}

ROW_FIELDS = {
    "Communication": '["name","reference_doctype","reference_name","subject","creation"]',
    "File": '["name","attached_to_doctype","attached_to_name","file_name","is_folder","creation"]',
}


def _rows(doctype: str, filters: str | None = None) -> list[dict]:
    params: dict = {
        "fields": ROW_FIELDS[doctype],
        "order_by": "creation asc",
        "limit_page_length": 500,
    }
    if filters:
        params["filters"] = filters
    with frappe_client() as client:
        response = client.get(f"/api/resource/{doctype}", params=params)
        response.raise_for_status()
        return response.json()["data"]


def _files_by_name(names: list[str]) -> list[dict]:
    """The named File rows, so purge can read `is_folder` before deleting."""
    return _rows("File", filters=json.dumps([["name", "in", list(names)]]))


def link_state(row: dict, doctype: str, supplier_names: set[str]) -> str:
    """Whether this row still points at a Supplier that exists.

    `unlinked` is not a defect. Rows created before the executor set a
    reference carry none at all, and an unlinked Communication is
    unattributable rather than orphaned -- a different thing to look at.
    """
    link_doctype, link_name = LINK_FIELDS[doctype]
    target_doctype = row.get(link_doctype)
    target_name = row.get(link_name)
    if not target_doctype or not target_name:
        return UNLINKED
    if target_doctype != "Supplier":
        return LINKED
    return LINKED if target_name in supplier_names else ORPHANED


def row_findings(row: dict, doctype: str, watch: dict[str, tuple[str, str]]) -> list[str]:
    """Watchlist matches on the supplier this row is filed under.

    Deleting a Supplier does NOT delete its correspondence or its
    attachments, so a sanctioned name outlives the record that carried it
    in a row the supplier-only audit never read. Compared whole, exactly as
    `cmd_suppliers` compares a supplier name -- these fail the audit.
    """
    _, link_name = LINK_FIELDS[doctype]
    target = str(row.get(link_name) or "")
    hit = watch.get(_norm(target)) if target else None
    if not hit:
        return []
    return [f"{doctype} {row['name']} is filed under {target!r} — matches {hit[0]} [{hit[1]}]"]


def row_mentions(row: dict, doctype: str, watch: dict[str, tuple[str, str]]) -> list[str]:
    """Watchlist names appearing inside the row's free text. WARN, not FAIL.

    A subject line normalises into one long key, so equality reads straight
    past a name embedded in it and substring is the only rule that finds
    one. That same rule cannot tell a real mention from a deliberate near
    miss: the watchlist alias 'Andes Verde' is a prefix of the legitimate
    demo supplier 'Andes Verde Import Export SAS', whose certificates would
    then fail every audit forever. A check that always fails stops being
    read, so these are surfaced for a human and left out of the exit code.
    """
    mentions: list[str] = []
    for field in TEXT_FIELDS[doctype]:
        value = str(row.get(field) or "")
        text = _norm(value)
        if not text:
            continue
        for key, (entity_id, topics) in watch.items():
            if key in text:
                mentions.append(
                    f"{doctype} {row['name']} {field} {value!r} — names {entity_id} [{topics}]"
                )
    return mentions


def protected_files(rows: list[dict]) -> list[str]:
    """File rows that must never be deleted: Frappe's own folder tree.

    `Home` and `Home/Attachments` are File records like any certificate is,
    and deleting one takes the site's attachment tree with it.
    """
    return [row["name"] for row in rows if row.get("is_folder")]


def cited_by_evidence(targets: list[str]) -> dict[str, list[str]]:
    """Which purge targets are named by a spike evidence file, and by which.

    Committed evidence is not a description of a proof, it IS the proof for
    anything asserted about deployed state — a case id, a command id, a
    supplier the ERP must hold exactly one of. Deleting such a record does
    not make a claim stale, it makes it unverifiable, and nothing about the
    deletion looks wrong at the time.

    That is not hypothetical. The day-7 hygiene pass (`0f5e831`) deleted 13
    cases by `DLQ-*` prefix and the supplier they named; on 2026-08-19 the
    core-contracts manifest went red because `one_erp_write_after_retry` had
    nothing left to read, and the proof had to be re-made from scratch
    (`spikes/core_contracts/redrill_retry.py`). The cleanup and the manifest
    depending on it were committed the same day.

    So this refuses rather than warns — the opposite of the watchlist
    substring rule above, and for the opposite reason. There, a substring hit
    cannot distinguish a real mention from the demo's deliberate near-miss,
    so it can only warn. Here the target is an exact record name the operator
    typed, and a file asserting something about it is enough: over-refusing
    costs one edit, under-refusing costs a gate.

    Every .json under spikes/ is read, not only committed ones, so a drill's
    fresh output protects its own records before anyone commits it.
    """
    if not targets:
        return {}
    citations: dict[str, list[str]] = {}
    for path in sorted(SPIKES.rglob("*.json")):
        try:
            text = path.read_text()
        except OSError:
            continue
        for target in targets:
            if target and target in text:
                citations.setdefault(target, []).append(
                    str(path.relative_to(SPIKES.parent))
                )
    return citations


def _cases(database: str = LIVE_DB) -> list[tuple[str, dict]]:
    db = get_client(database=database)
    return [(d.id, d.to_dict() or {}) for d in db.collection(CASES).stream()]


def cmd_suppliers(_args) -> int:
    rows = _suppliers()
    watch = _watchlist()
    print(f"{len(rows)} supplier(s) in the ERP")
    for row in rows:
        hit = watch.get(_norm(row["name"]))
        mark = f"  <-- MATCHES {hit[0]} [{hit[1]}]" if hit else ""
        print(f"  {row['creation'][:19]}  {row['name']}{mark}")
    return 0


def cmd_cases(args) -> int:
    rows = _cases(args.database)
    watch = _watchlist()
    print(f"{len(rows)} case(s) in {args.database}")
    for case_id, data in sorted(rows):
        supplier = str(data.get("supplier") or "")
        hit = watch.get(_norm(supplier)) if supplier else None
        mark = f"  <-- MATCHES {hit[0]} [{hit[1]}]" if hit else ""
        policy = (data.get("policy") or {}).get("band", "-")
        print(f"  {case_id:22} phase={data.get('phase')!s:18} band={policy!s:8} {supplier}{mark}")
    return 0


def cmd_links(args) -> int:
    """List the rows that hang off a Supplier: outbound mail and attachments.

    `suppliers` cannot show these and `audit` only summarises them, but they
    are what a Supplier delete trips over and what survives one.
    """
    watch = _watchlist()
    supplier_names = {row["name"] for row in _suppliers()}
    for doctype in ("Communication", "File"):
        rows = [row for row in _rows(doctype) if not row.get("is_folder")]
        _, link_name = LINK_FIELDS[doctype]
        text_field = TEXT_FIELDS[doctype][0]
        states = [link_state(row, doctype, supplier_names) for row in rows]
        summary = ", ".join(
            f"{states.count(state)} {state}" for state in (LINKED, ORPHANED, UNLINKED)
        )
        print(f"{len(rows)} {doctype} row(s) — {summary}")
        for row, state in zip(rows, states):
            target = row.get(link_name) or "-"
            mentions = row_mentions(row, doctype, watch)
            mark = "  <-- NAMES A WATCHLIST ENTITY" if mentions else ""
            print(
                f"  {row['creation'][:19]}  {row['name']:12} {state:9} "
                f"{target:38.38} {str(row.get(text_field) or '')[:44]}{mark}"
            )
    return 0


def cmd_audit(args) -> int:
    """Fail loudly if anything on record looks like a sanctions match."""
    watch = _watchlist()
    if not watch:
        print(f"FAIL  watchlist fixture missing at {WATCHLIST} — cannot audit")
        return 1

    findings: list[str] = []
    mentions: list[str] = []

    suppliers = _suppliers()
    supplier_names = {row["name"] for row in suppliers}
    for row in suppliers:
        hit = watch.get(_norm(row["name"]))
        if hit:
            findings.append(f"ERP supplier {row['name']!r} matches {hit[0]} [{hit[1]}]")

    for case_id, data in _cases(args.database):
        supplier = str(data.get("supplier") or "")
        hit = watch.get(_norm(supplier)) if supplier else None
        if hit:
            findings.append(f"case {case_id} supplier {supplier!r} matches {hit[0]} [{hit[1]}]")

    # Deleting a Supplier leaves its correspondence and its attachments in
    # place, so these rows are the one place a sanctioned name can hide from
    # every check above. Folder rows are site structure, not records.
    for doctype in ("Communication", "File"):
        rows = [row for row in _rows(doctype) if not row.get("is_folder")]
        orphaned = sum(
            1 for row in rows if link_state(row, doctype, supplier_names) == ORPHANED
        )
        if orphaned:
            print(
                f"WARN  {orphaned} orphaned {doctype} row(s) — the supplier they name is gone"
            )
        for row in rows:
            findings += row_findings(row, doctype, watch)
            mentions += row_mentions(row, doctype, watch)

    test_suppliers = [name for name in supplier_names if name.startswith("TEST Supplier")]
    if test_suppliers:
        print(f"WARN  {len(test_suppliers)} throwaway 'TEST Supplier' record(s) — purge before recording")

    if mentions:
        print(f"WARN  {len(mentions)} row(s) name a watchlist entity in free text:")
        for mention in mentions:
            print(f"        {mention}")
        print("      Substring match, so a deliberate near-miss name reads the same.")
        print("      Not part of the exit code — read them yourself.")

    if findings:
        print(f"FAIL  {len(findings)} sanctions match(es) on record:")
        for f in findings:
            print(f"        {f}")
        print("      A compliance demo must not show a sanctioned entity as onboarded.")
        return 1

    print("PASS  no watchlist entity is filed under a supplier, case, message or file")
    print("      (coarse normalised-name check — yente remains the real screen)")
    return 0


def cmd_purge(args) -> int:
    if not (args.test_suppliers or args.supplier or args.case or args.communication or args.file):
        print(
            "Nothing targeted. Pass --test-suppliers, --supplier NAME, --case ID, "
            "--communication ID, or --file ID."
        )
        return 2

    targets_suppliers: list[str] = []
    if args.test_suppliers:
        targets_suppliers += [
            r["name"] for r in _suppliers() if r["name"].startswith("TEST Supplier")
        ]
    if args.supplier:
        targets_suppliers += list(args.supplier)
    targets_cases = list(args.case or [])
    targets_comms = list(args.communication or [])
    targets_files = list(args.file or [])

    # Naming the target is normally the whole safeguard. It is not enough for
    # `Home` and `Home/Attachments`: those are File rows like any certificate,
    # and deleting one takes the site's entire attachment tree with it. This
    # runs before the dry-run print, so a typo cannot even be rehearsed.
    if targets_files:
        protected = protected_files(_files_by_name(targets_files))
        if protected:
            print(f"Refusing {len(protected)} folder row(s) — site file tree, not records:")
            for name in protected:
                print(f"  File          {name}")
            print("Nothing was deleted. Re-run without them.")
            return 2

    # Same placement and the same reason as the folder check above: this runs
    # before the dry-run print, so a purge that would destroy a proof cannot
    # even be rehearsed and then confirmed out of habit.
    cited = cited_by_evidence(
        targets_suppliers + targets_cases + targets_comms + targets_files
    )
    if cited:
        print(f"Refusing {len(cited)} target(s) — committed evidence asserts something about them:")
        for target, files in sorted(cited.items()):
            print(f"  {target}")
            for file in files:
                print(f"      cited by {file}")
        print(
            "\nNothing was deleted. Deleting these does not make a claim stale, it "
            "makes it unverifiable — this already cost the core-contracts manifest "
            "a criterion on 2026-08-19. If they must go, stop citing them first, "
            "then re-run the drill that replaces them."
        )
        return 2

    print(
        f"Would delete {len(targets_suppliers)} supplier(s), {len(targets_comms)} message(s), "
        f"{len(targets_files)} file(s) and {len(targets_cases)} case(s):"
    )
    for name in targets_comms:
        print(f"  message       {name}")
    for name in targets_files:
        print(f"  file          {name}")
    for name in targets_suppliers:
        print(f"  ERP supplier  {name}")
    for case_id in targets_cases:
        print(f"  case          {case_id}  (in {args.database}, recursive)")

    if not args.yes:
        print("\nDry run. Re-run with --yes to delete. Deletion is not reversible from here.")
        return 0

    # Correspondence and attachments first: a row still linked to a Supplier
    # can make the ERP refuse that Supplier's delete outright.
    erp_targets = (
        [("Communication", name) for name in targets_comms]
        + [("File", name) for name in targets_files]
        + [("Supplier", name) for name in targets_suppliers]
    )

    deleted, failed = 0, []
    if erp_targets:
        with frappe_client() as client:
            for doctype, name in erp_targets:
                quoted = urllib.parse.quote(name, safe="")
                response = client.delete(f"/api/resource/{doctype}/{quoted}")
                if response.status_code in (200, 202):
                    deleted += 1
                else:
                    failed.append((f"{doctype} {name}", response.status_code))

    db = get_client(database=args.database)
    for case_id in targets_cases:
        # Cases own inbox/outbox subcollections; a plain document delete would
        # orphan them rather than remove them.
        db.recursive_delete(db.collection(CASES).document(case_id))
        deleted += 1

    print(f"\ndeleted {deleted}")
    if failed:
        print(f"FAILED {len(failed)}:")
        for name, code in failed:
            print(f"  {code}  {name}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database",
        default=LIVE_DB,
        help=f"Firestore database for case operations (default: {LIVE_DB})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("suppliers", help="list ERP suppliers, flagging watchlist matches")
    sub.add_parser("cases", help="list case records, flagging watchlist matches")
    sub.add_parser("links", help="list Communication and File rows filed under a supplier")
    sub.add_parser("audit", help="exit non-zero if any watchlist entity is on record")

    purge = sub.add_parser("purge", help="delete targeted records (needs --yes)")
    purge.add_argument("--test-suppliers", action="store_true", help="all 'TEST Supplier *' records")
    purge.add_argument("--supplier", action="append", help="one supplier by exact name (repeatable)")
    purge.add_argument("--case", action="append", help="one case by id (repeatable)")
    purge.add_argument("--communication", action="append", help="one message by id (repeatable)")
    purge.add_argument("--file", action="append", help="one file by id (repeatable); folders refused")
    purge.add_argument("--yes", action="store_true", help="actually delete; without it this is a dry run")

    args = parser.parse_args()
    return {
        "suppliers": cmd_suppliers,
        "cases": cmd_cases,
        "links": cmd_links,
        "audit": cmd_audit,
        "purge": cmd_purge,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
