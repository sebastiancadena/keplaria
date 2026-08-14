#!/usr/bin/env python3
"""Maintenance for the demo ERP and the case records that mirror it.

Read-only by default. Every destructive action needs an explicit target AND
`--yes`; there is no "delete everything" verb, on purpose.

    uv run --env-file .env python scripts/erp.py suppliers
    uv run --env-file .env python scripts/erp.py cases
    uv run --env-file .env python scripts/erp.py audit
    uv run --env-file .env python scripts/erp.py purge --test-suppliers --yes
    uv run --env-file .env python scripts/erp.py purge --supplier "NAME" --yes
    uv run --env-file .env python scripts/erp.py purge --case TV-XXXX --yes

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


def cmd_audit(args) -> int:
    """Fail loudly if anything on record looks like a sanctions match."""
    watch = _watchlist()
    if not watch:
        print(f"FAIL  watchlist fixture missing at {WATCHLIST} — cannot audit")
        return 1

    findings: list[str] = []

    for row in _suppliers():
        hit = watch.get(_norm(row["name"]))
        if hit:
            findings.append(f"ERP supplier {row['name']!r} matches {hit[0]} [{hit[1]}]")

    for case_id, data in _cases(args.database):
        supplier = str(data.get("supplier") or "")
        hit = watch.get(_norm(supplier)) if supplier else None
        if hit:
            findings.append(f"case {case_id} supplier {supplier!r} matches {hit[0]} [{hit[1]}]")

    test_suppliers = [r["name"] for r in _suppliers() if r["name"].startswith("TEST Supplier")]
    if test_suppliers:
        print(f"WARN  {len(test_suppliers)} throwaway 'TEST Supplier' record(s) — purge before recording")

    if findings:
        print(f"FAIL  {len(findings)} sanctions match(es) on record:")
        for f in findings:
            print(f"        {f}")
        print("      A compliance demo must not show a sanctioned entity as onboarded.")
        return 1

    print("PASS  no watchlist entity appears in the ERP or the live case store")
    print("      (coarse normalised-name check — yente remains the real screen)")
    return 0


def cmd_purge(args) -> int:
    if not (args.test_suppliers or args.supplier or args.case):
        print("Nothing targeted. Pass --test-suppliers, --supplier NAME, or --case ID.")
        return 2

    targets_erp: list[str] = []
    if args.test_suppliers:
        targets_erp += [r["name"] for r in _suppliers() if r["name"].startswith("TEST Supplier")]
    if args.supplier:
        targets_erp += list(args.supplier)
    targets_cases = list(args.case or [])

    print(f"Would delete {len(targets_erp)} supplier(s) and {len(targets_cases)} case(s):")
    for name in targets_erp:
        print(f"  ERP supplier  {name}")
    for case_id in targets_cases:
        print(f"  case          {case_id}  (in {args.database}, recursive)")

    if not args.yes:
        print("\nDry run. Re-run with --yes to delete. Deletion is not reversible from here.")
        return 0

    deleted, failed = 0, []
    if targets_erp:
        with frappe_client() as client:
            for name in targets_erp:
                quoted = urllib.parse.quote(name, safe="")
                response = client.delete(f"/api/resource/Supplier/{quoted}")
                if response.status_code in (200, 202):
                    deleted += 1
                else:
                    failed.append((name, response.status_code))

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
    sub.add_parser("audit", help="exit non-zero if any watchlist entity is on record")

    purge = sub.add_parser("purge", help="delete targeted records (needs --yes)")
    purge.add_argument("--test-suppliers", action="store_true", help="all 'TEST Supplier *' records")
    purge.add_argument("--supplier", action="append", help="one supplier by exact name (repeatable)")
    purge.add_argument("--case", action="append", help="one case by id (repeatable)")
    purge.add_argument("--yes", action="store_true", help="actually delete; without it this is a dry run")

    args = parser.parse_args()
    return {
        "suppliers": cmd_suppliers,
        "cases": cmd_cases,
        "audit": cmd_audit,
        "purge": cmd_purge,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
