"""Re-establish the deployed proof that a retried ERP write stays singular.

Run: uv run --env-file .env python spikes/core_contracts/redrill_retry.py

`one_erp_write_after_retry` is the one criterion in this manifest that cannot
be proven from committed files: it asserts something about the state of the
deployed ledger and the live ERP, so its proof is a pair of records rather
than a document. On 2026-08-19 that pair stopped existing. The day-7 ERP
hygiene pass deleted 13 cases by prefix — DLQ-* among them — and the supplier
they name, which is exactly what this criterion reads. Nothing regressed; the
evidence was thrown away by a cleanup committed the same day as the manifest
depending on it. A scan of all 39 surviving outbox rows found no other command
with `status: done` and `execution_attempts >= 1`, so the proof had to be
made again rather than found.

This script makes it, the same way `spikes/dlq/harness.py` made it the first
time and by importing that harness rather than restating it. The failure is
real, not injected: `clear_hold` against a supplier absent from the ERP is
refused by Frappe with a 404. The repair is real too — the supplier is then
created out of band, standing in for the human who fixes whatever broke — and
the DEPLOYED sweep, not this script, is what notices the failed command and
drives it to done. Editing the command by hand would produce the same row and
prove nothing.

Safe to re-run, and it refuses rather than lying if it cannot produce a real
failure: the drill needs the supplier ABSENT at the start, because a supplier
that already exists makes the first drain succeed, leaving
`execution_attempts` at 0 and the criterion still unproven — while every
individual step reports success. That is the failure mode this file most
needs to avoid, so it is checked first and aborts.

Writes spikes/core_contracts/retry_drill.json. That file is also what stops
this happening again: `scripts/erp.py purge` refuses to delete anything named
in a committed evidence file, and this run's case, command, and supplier are
named there.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.executor.frappe import create_supplier_if_absent, frappe_client  # noqa: E402
from app.executor.runner import execute_pending_commands  # noqa: E402
from app.lifecycle import CLEAR_HOLD  # noqa: E402
from app.state.commands import DONE, FAILED, claim_command, command_id, get_command  # noqa: E402
from app.state.firestore import get_client  # noqa: E402


def _dlq_harness():
    """Load spikes/dlq/harness.py by path — spikes/ is not a package.

    Imported rather than copied so `seed_case`'s notion of a minimal case
    document, and the ingress URL and supplier name this drill must match,
    cannot drift from the harness that first produced this evidence. Its
    module level is constants and imports only; loading it runs no checks.
    """
    spec = importlib.util.spec_from_file_location(
        "dlq_harness", ROOT / "spikes" / "dlq" / "harness.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def supplier_records(client, name: str) -> list[dict]:
    response = client.get(
        "/api/resource/Supplier",
        params={
            "filters": json.dumps([["name", "=", name]]),
            "fields": '["name","on_hold"]',
            "limit_page_length": 100,
        },
    )
    response.raise_for_status()
    return response.json()["data"]


def main() -> int:
    dlq = _dlq_harness()
    supplier = dlq.SWEEP_SUPPLIER
    db = get_client(database="(default)")

    with frappe_client() as client:
        existing = supplier_records(client, supplier)
    if existing:
        print(
            f"REFUSING: {supplier!r} already exists in the ERP.\n"
            "The first drain would then SUCCEED, leaving execution_attempts at 0, "
            "and this drill would report every step green while proving nothing "
            "about retry. Delete that supplier deliberately (scripts/erp.py purge "
            f"--supplier {supplier!r} --yes) and re-run, or use the command this "
            "criterion already has."
        )
        return 2

    case_id = f"DLQ-SWEEP-{uuid.uuid4().hex[:8].upper()}"
    cmd_id = command_id(case_id, CLEAR_HOLD, 1)
    print(f"case {case_id}\ncommand {cmd_id}\nsupplier {supplier}\n")

    dlq.seed_case(db, case_id, band="clear")
    claim_command(db, case_id, CLEAR_HOLD, 1, {"supplier_name": supplier})

    first = execute_pending_commands(db, case_id)
    failed = get_command(db, case_id, CLEAR_HOLD, 1) or {}
    attempts = int(failed.get("execution_attempts") or 0)
    print(f"first drain: status={failed.get('status')} attempts={attempts}")
    if failed.get("status") != FAILED or attempts < 1:
        print("ABORT: the first drain did not fail, so there is no retry to prove.")
        return 1

    with frappe_client() as client:
        created = create_supplier_if_absent(
            client, supplier, email_id="dlq-sweep-probe@example.com"
        )
    print(f"repaired out of band: {created}")

    response = httpx.post(
        f"{dlq.INGRESS_URL}/admin/sweep",
        headers={"Authorization": f"Bearer {dlq.id_token()}"},
        json={},
        timeout=300,
    )
    summary = response.json() if response.status_code == 200 else {"error": response.text[:300]}
    after = get_command(db, case_id, CLEAR_HOLD, 1) or {}
    print(f"sweep HTTP {response.status_code}: {json.dumps(summary)[:200]}")
    print(f"after sweep: status={after.get('status')} attempts={after.get('execution_attempts')}")

    with frappe_client() as client:
        records = supplier_records(client, supplier)

    passed = (
        after.get("status") == DONE
        and int(after.get("execution_attempts") or 0) >= 1
        and case_id in (summary.get("case_ids") or [])
        and len(records) == 1
        and not records[0].get("on_hold")
    )

    evidence = {
        "drill": "one_erp_write_after_retry",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if passed else "FAIL",
        "why_this_exists": (
            "The criterion's original records (DLQ-SWEEP-43CDC293 and its "
            "supplier) were deleted by the day-7 ERP hygiene pass 0f5e831, "
            "which removed cases by DLQ-* prefix. The contract never "
            "regressed; its evidence was deleted. This run re-made it."
        ),
        "case_id": case_id,
        "command_id": cmd_id,
        "supplier": supplier,
        "observed": {
            "first_drain": [r for r in first if r.get("action") == CLEAR_HOLD],
            "status_after_first_drain": failed.get("status"),
            "execution_attempts_after_first_drain": attempts,
            "supplier_created_out_of_band": created,
            "sweep_http_status": response.status_code,
            "sweep_summary": summary,
            "status_after_sweep": after.get("status"),
            "execution_attempts_after_sweep": after.get("execution_attempts"),
            "erp_records_named_supplier": records,
        },
        "do_not_delete": (
            "This case, command and supplier ARE the proof of "
            "one_erp_write_after_retry. scripts/erp.py purge refuses targets "
            "named in a committed evidence file; if they must ever go, stop "
            "citing them here first, then re-run this drill to replace them."
        ),
    }
    (HERE / "retry_drill.json").write_text(json.dumps(evidence, indent=1) + "\n")

    print(f"\nRESULT: {evidence['result']} — spikes/core_contracts/retry_drill.json")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
