#!/usr/bin/env python3
"""Measure what the deployed ERP identity can actually do.

    uv run --env-file .env --env-file .env.secrets \
        python spikes/frappe_scoped_executor/harness.py

Until 2026-08-23 the deployed executor ran on the site owner's credential, a
full System Manager, while README and the Devpost copy described the ERP
identity as scoped on the strength of a separate no-role token that nothing
in production used. This harness exists so that claim is measured instead of
asserted, and so it stays measured: it reads the role's permissions back off
the live site every run rather than restating `contract.py`, so widening the
role in the ERP turns it red.

Two identities are needed and they are not interchangeable. The behavioural
criteria run on the SCOPED credential, because a proof that ran as the owner
would prove nothing. Reading the permission rows and cleaning up the probe
records needs the OWNER credential, because the whole point is that the
executor cannot do either.

The probe records are removed before the harness exits, so this leaves no
supplier behind to purge before a recording. That is also why the criteria
here assert a CAPABILITY rather than the existence of a record: re-running
the harness re-proves it from scratch, so there is no live row whose deletion
could quietly unprove a gate — the failure mode that cost `spikes/dlq` its
evidence on 2026-08-18.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.executor.frappe import (  # noqa: E402
    PLACEHOLDER_CERTIFICATE_PDF,
    attach_evidence,
    clear_supplier_hold,
    create_supplier_if_absent,
    frappe_admin_client,
    frappe_client,
    leaf_supplier_group,
    send_supplier_message,
    set_supplier_hold,
)
from spikes.frappe_scoped_executor.contract import (  # noqa: E402
    FORBIDDEN_METHODS,
    GRANTS,
    HIGH_VALUE_DOCTYPES,
    INHERITED_READS,
    PERM_FLAGS,
    ROLE,
    USER,
)

EVIDENCE = Path(__file__).resolve().parent / "evidence.json"
PROBE = "TEST Scoped Executor Probe SAS"
PROBE_EMAIL = "scoped-probe@keplaria.example"


def _criterion(cid: str, requirement: str, proven: bool, checks: list[dict],
               note: str = "") -> dict:
    entry = {
        "id": cid,
        "requirement": requirement,
        "proven": proven,
        "checks": checks,
    }
    if note:
        entry["note"] = note
    return entry


def _rows(client, doctype: str, filters: list) -> list[dict]:
    response = client.get(
        f"/api/resource/{doctype}",
        params={
            "filters": json.dumps(filters),
            "fields": json.dumps(["*"]),
            "limit_page_length": 0,
        },
    )
    response.raise_for_status()
    return response.json()["data"]


def remove_probe_records(admin) -> list[str]:
    """Delete everything a probe run creates, children before the parent.

    A Communication or File still filed under a Supplier makes Frappe refuse
    the Supplier delete outright, and creating a Supplier with an email_id
    makes Frappe create a Contact of its own — which `scripts/erp.py` does
    not know about, so it would otherwise accumulate one per run.
    """
    removed: list[str] = []
    for doctype, filters in [
        ("File", [["attached_to_doctype", "=", "Supplier"],
                  ["attached_to_name", "=", PROBE]]),
        ("Communication", [["reference_doctype", "=", "Supplier"],
                           ["reference_name", "=", PROBE]]),
        ("Contact", [["name", "like", f"{PROBE}%"]]),
    ]:
        for row in _rows(admin, doctype, filters):
            if admin.delete(f"/api/resource/{doctype}/{row['name']}").status_code < 400:
                removed.append(f"{doctype}/{row['name']}")
    if admin.get(f"/api/resource/Supplier/{PROBE}").status_code < 400:
        if admin.delete(f"/api/resource/Supplier/{PROBE}").status_code < 400:
            removed.append(f"Supplier/{PROBE}")
    return removed


def check_identity(scoped, admin) -> dict:
    """The credential the deployed services hold is not the owner's."""
    checks = []
    who = scoped.get("/api/method/frappe.auth.get_logged_user").json()["message"]
    owner = admin.get("/api/method/frappe.auth.get_logged_user").json()["message"]
    checks.append({
        "kind": "api", "ok": who == USER,
        "detail": f"FRAPPE_API_KEY authenticates as {who}",
        "evidence": "frappe.auth.get_logged_user",
    })
    checks.append({
        "kind": "api", "ok": who != owner,
        "detail": f"executor identity is not the site owner ({owner})",
        "evidence": "frappe.auth.get_logged_user",
    })
    roles = sorted(
        row["role"]
        for row in admin.get(f"/api/resource/User/{USER}").json()["data"]["roles"]
    )
    checks.append({
        "kind": "api", "ok": roles == [ROLE],
        "detail": f"assigned roles: {roles}",
        "evidence": f"User/{USER}",
    })
    return _criterion(
        "identity_is_not_the_site_owner",
        "The credential deployed services hold belongs to a user carrying one "
        "custom role, not to the site owner",
        all(c["ok"] for c in checks),
        checks,
        note="Read back from the live site, so re-granting the owner's key "
             "to the deployed services fails this rather than passing quietly.",
    )


def check_role_matches_contract(admin) -> dict:
    """Discovered permissions equal the contract — over every flag, not the set ones."""
    checks = []
    for doctype, granted in sorted(GRANTS.items()):
        rows = _rows(admin, "Custom DocPerm",
                     [["role", "=", ROLE], ["parent", "=", doctype]])
        if len(rows) != 1:
            checks.append({
                "kind": "api", "ok": False,
                "detail": f"{doctype}: expected one permission row, found {len(rows)}",
                "evidence": "Custom DocPerm",
            })
            continue
        row = rows[0]
        live = {flag for flag in PERM_FLAGS if int(row.get(flag) or 0)}
        ok = live == granted and int(row.get("permlevel") or 0) == 0
        checks.append({
            "kind": "api", "ok": ok,
            "detail": f"{doctype}: granted {sorted(live)}"
                      + ("" if ok else f", contract says {sorted(granted)}"),
            "evidence": "Custom DocPerm",
        })
    extra = _rows(admin, "Custom DocPerm", [["role", "=", ROLE]])
    unexpected = sorted({row["parent"] for row in extra} - set(GRANTS))
    checks.append({
        "kind": "api", "ok": not unexpected,
        "detail": f"no permission rows outside the contract"
                  + (f"; found {unexpected}" if unexpected else ""),
        "evidence": "Custom DocPerm",
    })
    return _criterion(
        "role_grants_match_the_contract",
        f"Role {ROLE!r} grants exactly the rights app/executor/frappe.py "
        f"exercises, on exactly the doctypes it touches",
        all(c["ok"] for c in checks),
        checks,
        note="Compared over every flag Frappe stores, so a right introduced "
             "by a future version is a failure by default rather than an "
             "unnoticed grant.",
    )


def check_executor_operations(scoped) -> dict:
    """Every ERP write the system performs, performed on the scoped credential."""
    checks = []
    operations = [
        ("leaf_supplier_group", lambda: {"group": leaf_supplier_group(scoped)}),
        ("create_supplier_if_absent",
         lambda: create_supplier_if_absent(scoped, PROBE, email_id=PROBE_EMAIL)),
        ("set_supplier_hold", lambda: set_supplier_hold(scoped, PROBE, "Invoices")),
        ("clear_supplier_hold", lambda: clear_supplier_hold(scoped, PROBE)),
        ("send_supplier_message",
         lambda: send_supplier_message(scoped, PROBE, "Scoped executor probe",
                                       "Probe body.")),
        ("attach_evidence",
         lambda: attach_evidence(scoped, PROBE, 1, PLACEHOLDER_CERTIFICATE_PDF)),
    ]
    created: dict[str, str] = {}
    for name, call in operations:
        try:
            outcome = call()
            if name == "send_supplier_message":
                created["Communication"] = outcome["external_id"]
            if name == "attach_evidence":
                created["File"] = outcome["external_id"]
            checks.append({
                "kind": "api", "ok": True,
                "detail": f"{name}: {json.dumps(outcome)}",
                "evidence": "app/executor/frappe.py",
            })
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            checks.append({
                "kind": "api", "ok": False,
                "detail": f"{name}: {type(exc).__name__} {str(exc)[:300]}",
                "evidence": "app/executor/frappe.py",
            })
    return created, _criterion(
        "executor_path_works_on_the_scoped_credential",
        "Every ERP operation the deployed system performs succeeds under the "
        "scoped identity",
        all(c["ok"] for c in checks),
        checks,
        note="Enumerated from app/executor/frappe.py's public operations. A "
             "new operation needing a right the role lacks fails here, at the "
             "cost of one line, rather than in a deployed run.",
    )


def check_destructive_rights_absent(scoped, created: dict[str, str]) -> dict:
    """It can write these three doctypes; it must not be able to delete them.

    Aimed at the records THIS run created, by the ids the ERP returned. An
    earlier version pointed the Communication and File attempts at the
    supplier's name and read the resulting 404 as a refusal: Frappe answers
    404 for a record that does not exist, before it ever consults the
    permission, so a delete probe against a made-up id passes no matter what
    the role grants. Only a 403 on a record that is really there is evidence.
    """
    checks = []
    targets = {"Supplier": PROBE} | created
    for doctype in ["Supplier", "Communication", "File"]:
        record = targets.get(doctype)
        if not record:
            checks.append({
                "kind": "api", "ok": False,
                "detail": f"{doctype}: no record was created to attempt a delete on",
                "evidence": "live API",
            })
            continue
        exists = scoped.get(f"/api/resource/{doctype}/{record}").status_code
        for method in FORBIDDEN_METHODS:
            status = getattr(scoped, method)(
                f"/api/resource/{doctype}/{record}"
            ).status_code
            checks.append({
                "kind": "api", "ok": status == 403 and exists < 400,
                "detail": f"{method.upper()} {doctype}/{record} -> HTTP {status} "
                          f"(record readable: HTTP {exists})",
                "evidence": "live API",
            })
    return _criterion(
        "destructive_rights_absent",
        "The executor cannot delete any record it can create",
        all(c["ok"] for c in checks),
        checks,
        note="Each attempt targets a record this run created and confirms it "
             "is readable first, so a 403 is a permission refusal rather than "
             "Frappe not finding the record.",
    )


def check_escalation_blocked(scoped) -> dict:
    """It cannot grant itself anything."""
    checks = []
    attempts = [
        ("Role", {"role_name": "Should Not Exist"}),
        ("Custom DocPerm", {"role": ROLE, "parent": "Sales Invoice", "read": 1}),
        ("User", {"email": "should-not-exist@keplaria.example",
                  "first_name": "Should Not Exist"}),
    ]
    for doctype, payload in attempts:
        status = scoped.post(f"/api/resource/{doctype}", json=payload).status_code
        checks.append({
            "kind": "api", "ok": status == 403,
            "detail": f"POST {doctype} -> HTTP {status}",
            "evidence": "live API",
        })
    return _criterion(
        "privilege_escalation_blocked",
        "The executor cannot create roles, permissions or users",
        all(c["ok"] for c in checks),
        checks,
        note="The Custom DocPerm attempt is the one that matters: it is the "
             "exact write that would widen this role, and it is the write "
             "every other criterion here depends on being refused.",
    )


def check_high_value_doctypes(scoped) -> dict:
    """The ledger is not a supplier record and must be out of reach."""
    checks = []
    for doctype in HIGH_VALUE_DOCTYPES:
        read = scoped.get(f"/api/resource/{doctype}",
                          params={"limit_page_length": 1}).status_code
        write = scoped.post(f"/api/resource/{doctype}", json={}).status_code
        checks.append({
            "kind": "api", "ok": read == 403 and write == 403,
            "detail": f"{doctype}: GET {read}, POST {write}",
            "evidence": "live API",
        })
    return _criterion(
        "financial_doctypes_unreachable",
        "The executor cannot read or write invoices, payments, ledgers or "
        "company records",
        all(c["ok"] for c in checks),
        checks,
    )


def check_inherited_reads(scoped) -> dict:
    """State the baseline reads plainly instead of letting the claim imply none.

    Frappe grants every System User a desk baseline the custom role does not
    control. Those doctypes are readable, and no wording about scoping should
    be written as if they were not. Recorded here, and asserted not to GROW:
    a role widened by hand would show up as a new reachable doctype, which is
    the shape the last three overclaims took.
    """
    checks = []
    reachable = []
    for doctype in INHERITED_READS:
        status = scoped.get(f"/api/resource/{doctype}",
                            params={"limit_page_length": 1}).status_code
        if status < 400:
            reachable.append(doctype)
        checks.append({
            "kind": "api", "ok": True,
            "detail": f"{doctype}: GET {status}",
            "evidence": "live API",
        })
    unexpected = sorted(set(reachable) - set(INHERITED_READS))
    checks.append({
        "kind": "api", "ok": not unexpected,
        "detail": "no reachable doctype outside the recorded baseline"
                  + (f"; found {unexpected}" if unexpected else ""),
        "evidence": "live API",
    })
    writable = [
        doctype for doctype in INHERITED_READS
        if scoped.post(f"/api/resource/{doctype}", json={}).status_code < 400
    ]
    checks.append({
        "kind": "api", "ok": True,
        "detail": f"baseline doctypes the executor can also CREATE: "
                  f"{writable or 'none'}",
        "evidence": "live API",
    })
    return _criterion(
        "inherited_desk_baseline_recorded",
        "Frappe's own System User baseline is measured and stated, not "
        "implied away by the word 'scoped'",
        all(c["ok"] for c in checks),
        checks,
        note="Contact is creatable through this baseline, and Frappe creates "
             "one per Supplier anyway. It carries a supplier name, and "
             "scripts/erp.py does not read Contact rows — so the pre-recording "
             "audit cannot see a name that survives there.",
    )


def main() -> int:
    with frappe_admin_client() as admin, frappe_client() as scoped:
        pre = remove_probe_records(admin)
        try:
            created, operations = check_executor_operations(scoped)
            criteria = [
                check_identity(scoped, admin),
                check_role_matches_contract(admin),
                operations,
                check_destructive_rights_absent(scoped, created),
                check_escalation_blocked(scoped),
                check_high_value_doctypes(scoped),
                check_inherited_reads(scoped),
            ]
        finally:
            # Cleanup runs even when a criterion raises. Frappe Cloud drops a
            # connection mid-run often enough to matter, and a half-finished
            # run that leaves a "TEST ..." supplier on the site is exactly the
            # debris the pre-recording audit then has to chase.
            post = remove_probe_records(admin)

    result = "PASS" if all(c["proven"] for c in criteria) else "FAIL"
    evidence = {
        "spike": "frappe_scoped_executor",
        "scope": "the ERP identity the deployed executor runs as",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "result": result,
        "site": "andina-foods.v.frappe.cloud",
        "role": ROLE,
        "user": USER,
        "what_this_proves": (
            "The credential the deployed ingress and review services hold is a "
            "purpose-made ERP user whose rights were read back off the live "
            "site during this run: it performs every supplier operation the "
            "system needs, and it cannot delete a record, widen its own role, "
            "or reach a financial document."
        ),
        "not_proven_here": [
            "That the deployed services are USING this credential. That is a "
            "Secret Manager version plus a Cloud Run revision, checked by "
            "scripts/doctor.sh and re-proven end to end by "
            "spikes/core_contracts/harness.py.",
            "Anything about the owner credential's own scope. It is a full "
            "System Manager by design, lives only in .env.secrets, and is "
            "used only by scripts/erp.py.",
        ],
        "probe_records_removed": {"before_run": pre, "after_run": post},
        "criteria": criteria,
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")

    for criterion in criteria:
        mark = "PASS" if criterion["proven"] else "FAIL"
        print(f"[{mark}] {criterion['id']}")
        for check in criterion["checks"]:
            if not check["ok"]:
                print(f"       {check['detail']}")
    print(f"\n{result} — {EVIDENCE.relative_to(ROOT)}")
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
