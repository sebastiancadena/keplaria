#!/usr/bin/env python3
"""Per-SKU cost attribution from the BigQuery billing export.

The month-to-date totals `collect.sh` reads from budget notifications answer
"what has this cost", which is not the question the judging-window posture
turns on. That question is "what does each hour of uptime cost, and what
accrues whether or not anything is running" -- and only per-SKU usage answers
it.

Two things here are deliberate:

* **The table is derived from the billing account, never named.** An export
  recreated under a different account would otherwise return no rows, and no
  rows is indistinguishable from no cost.
* **Rates come from the last COMPLETE day, not the newest one.** The export
  lags roughly a day, so the newest day in the table is routinely partial. A
  partial day looks exactly like a cheap day: on 2026-08-21 the 60GB boot disk
  billed 2.6e15 byte-seconds instead of a full day's 5.6e15, which would have
  halved the standing rate and made always-on look affordable for the wrong
  reason.

Usage:
    python3 sku_rates.py --billing-account 006252-CAF71A-EC72B9 --vcpus 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

# SKUs metered per instance-second. These are the only ones that stop accruing
# when the VM stops, so they are the only ones multiplied by uptime hours in a
# projection. Everything else -- disk, NAT, snapshot storage -- bills whether
# the instance runs or not, which is the whole reason the two are separated.
UPTIME_SKU_MARKERS = ("Instance Core running", "Instance Ram running")

# Event-driven transfer, not a daily rate. A recovery drill downloads a
# snapshot once and it would otherwise be smeared across every projected day.
ONE_OFF_SKU_MARKERS = ("Snapshot download", "Snapshot upload")

QUERY = """
SELECT
  FORMAT_DATE('%Y-%m-%d', DATE(usage_start_time)) AS day,
  service.description AS service,
  sku.description AS sku,
  SUM(cost) AS gross_usd,
  SUM(usage.amount) AS usage_amount,
  ANY_VALUE(usage.unit) AS usage_unit
FROM `{table}`
GROUP BY day, service, sku
ORDER BY day, gross_usd DESC
"""


def run_query(table: str, project: str) -> list[dict]:
    out = subprocess.run(
        ["bq", f"--project_id={project}", "query", "--nouse_legacy_sql",
         "--format=json", "--max_rows=10000", QUERY.format(table=table)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "bq query failed")
    return json.loads(out.stdout or "[]")


def classify(sku: str) -> str:
    if any(m in sku for m in UPTIME_SKU_MARKERS):
        return "uptime"
    if any(m in sku for m in ONE_OFF_SKU_MARKERS):
        return "one_off"
    return "standing"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--billing-account", required=True)
    ap.add_argument("--project", default="keplaria")
    ap.add_argument("--vcpus", type=int, required=True,
                    help="vCPUs of the scheduled VM; converts metered "
                         "core-seconds into instance-hours")
    args = ap.parse_args()

    table = (f"{args.project}.billing_export.gcp_billing_export_resource_v1_"
             f"{args.billing_account.replace('-', '_')}")

    try:
        rows = run_query(table, args.project)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        json.dump({"available": False, "reason": str(exc), "table": table},
                  sys.stdout, indent=2)
        print()
        return 0

    days = sorted({r["day"] for r in rows})
    if len(days) < 2:
        json.dump({"available": False, "table": table,
                   "reason": "export holds fewer than two days, so no day is "
                             "known-complete; it is forward-only and never "
                             "backfills",
                   "days_present": days}, sys.stdout, indent=2)
        print()
        return 0

    # Second-newest day: the newest is still being written to.
    rate_day = days[-2]
    on_rate_day = [r for r in rows if r["day"] == rate_day]

    core_seconds = 0.0
    uptime_usd = standing_usd = one_off_usd = 0.0
    breakdown = []
    for r in on_rate_day:
        cost = float(r["gross_usd"])
        kind = classify(r["sku"])
        if kind == "uptime":
            uptime_usd += cost
            if "Instance Core running" in r["sku"]:
                core_seconds += float(r["usage_amount"])
        elif kind == "one_off":
            one_off_usd += cost
        else:
            standing_usd += cost
        if cost > 0:
            breakdown.append({
                "sku": r["sku"], "service": r["service"], "kind": kind,
                "gross_usd": round(cost, 6),
                "usage_amount": float(r["usage_amount"]),
                "usage_unit": r["usage_unit"],
            })

    instance_hours = core_seconds / args.vcpus / 3600 if args.vcpus else 0.0
    vm_hourly = uptime_usd / instance_hours if instance_hours else None

    # The pinned Agent Engine. A held instance would meter 86400 vCPU-seconds a
    # day; anything far below that means the meter tracks request time and the
    # minInstances pin is free.
    agent_rows = [r for r in rows if r["sku"].startswith("Agent Platform")]
    agent = {
        "skus_present": sorted({r["sku"] for r in agent_rows}),
        "gross_usd_all_days": round(sum(float(r["gross_usd"])
                                        for r in agent_rows), 6),
        "compute_seconds_by_day": {
            r["day"]: float(r["usage_amount"]) for r in agent_rows
            if r["sku"] == "Agent Platform Compute"
        },
        "note": "Agent Platform Compute meters request-processing time, not "
                "held instances: a pinned minInstances=1 would meter 86400 "
                "vCPU-seconds per day. Measured cost is the authority here, "
                "not the published per-hour rate.",
    }

    doc = {
        "available": True,
        "table": table,
        "days_present": days,
        "rate_day": rate_day,
        "rate_day_note": "second-newest day in the export; the newest is "
                         "partial and would understate every standing rate",
        "vm_vcpus": args.vcpus,
        "vm_instance_hours_on_rate_day": round(instance_hours, 3),
        "vm_hourly_usd": round(vm_hourly, 4) if vm_hourly else None,
        "standing_daily_usd": round(standing_usd, 4),
        "one_off_usd_on_rate_day": round(one_off_usd, 4),
        "one_off_note": "event-driven transfer (a recovery drill's snapshot "
                        "restore); excluded from every daily rate",
        "sku_breakdown_on_rate_day": breakdown,
        "agent_platform": agent,
    }
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
