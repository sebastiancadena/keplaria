#!/usr/bin/env python3
"""Provision the scoped executor identity on the live Frappe site.

Idempotent and safe to re-run: it creates the role, sets exactly the
permissions `contract.py` declares (turning OFF anything Frappe granted by
default that the contract does not list), and creates the user holding that
role and nothing else.

Key generation is deliberately NOT part of that. `--generate-keys` rotates
the credential, which breaks every deployed service until Secret Manager and
the Cloud Run revisions catch up, so it has to be asked for.

    uv run --env-file .env --env-file .env.secrets \
        python spikes/frappe_scoped_executor/provision.py
    uv run --env-file .env --env-file .env.secrets \
        python spikes/frappe_scoped_executor/provision.py --generate-keys

Runs as the site owner: creating roles and users is exactly what the
executor identity must not be able to do.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.executor.frappe import frappe_admin_client  # noqa: E402
from spikes.frappe_scoped_executor.contract import (  # noqa: E402
    BASELINE_REVOCATIONS,
    GRANTS,
    PERM_FLAGS,
    ROLE,
    USER,
)


def ensure_role(client) -> str:
    if client.get(f"/api/resource/Role/{ROLE}").status_code < 400:
        return "present"
    response = client.post(
        "/api/resource/Role",
        json={"role_name": ROLE, "desk_access": 1, "is_custom": 1},
    )
    response.raise_for_status()
    return "created"


def ensure_permissions(client) -> dict[str, str]:
    """Make the live permission rows equal the contract, flag by flag.

    Frappe's `add` seeds a new row with read and export set. Export is a
    data-egress right the executor has no use for, so this walks every flag
    in PERM_FLAGS rather than only the ones being granted: a right the
    contract does not name is turned off whether Frappe set it or a person
    did.
    """
    outcome: dict[str, str] = {}
    for doctype, granted in GRANTS.items():
        existing = client.get(
            "/api/resource/Custom DocPerm",
            params={
                "filters": json.dumps(
                    [["role", "=", ROLE], ["parent", "=", doctype]]
                ),
                "fields": json.dumps(["*"]),
                "limit_page_length": 0,
            },
        )
        existing.raise_for_status()
        rows = existing.json()["data"]
        if not rows:
            response = client.post(
                "/api/method/frappe.core.page.permission_manager.permission_manager.add",
                data={"parent": doctype, "role": ROLE, "permlevel": 0},
            )
            response.raise_for_status()
            outcome[doctype] = "created"
            rows = [{flag: 0 for flag in PERM_FLAGS} | {"read": 1, "export": 1}]
        else:
            outcome[doctype] = "present"

        current = rows[0]
        for flag in PERM_FLAGS:
            want = 1 if flag in granted else 0
            if int(current.get(flag) or 0) == want:
                continue
            response = client.post(
                "/api/method/frappe.core.page.permission_manager.permission_manager.update",
                data={
                    "doctype": doctype,
                    "role": ROLE,
                    "permlevel": 0,
                    "ptype": flag,
                    "value": want,
                },
            )
            response.raise_for_status()
            outcome[doctype] = "adjusted"
    return outcome


def revoke_baseline_rights(client) -> dict[str, str]:
    """Take back the deletes Frappe grants every authenticated user.

    A custom role cannot scope these away, because `All` is implicit: it is
    held by the executor, by the unprivileged spike token, and by any user
    added later. Revoking them is what makes "the executor cannot delete a
    record" a measured fact rather than a description of the custom role
    alone.
    """
    outcome: dict[str, str] = {}
    for doctype, role, flag in BASELINE_REVOCATIONS:
        rows = client.get(
            "/api/resource/Custom DocPerm",
            params={
                "filters": json.dumps(
                    [["role", "=", role], ["parent", "=", doctype],
                     ["permlevel", "=", 0]]
                ),
                "fields": json.dumps(["*"]),
                "limit_page_length": 0,
            },
        )
        rows.raise_for_status()
        data = rows.json()["data"]
        key = f"{doctype}/{role}/{flag}"
        if not data:
            outcome[key] = "no such permission row"
            continue
        if not int(data[0].get(flag) or 0):
            outcome[key] = "already revoked"
            continue
        response = client.post(
            "/api/method/frappe.core.page.permission_manager.permission_manager.update",
            data={"doctype": doctype, "role": role, "permlevel": 0,
                  "ptype": flag, "value": 0},
        )
        response.raise_for_status()
        outcome[key] = "revoked"
    return outcome


def ensure_user(client) -> str:
    """The executor user, holding the contract role and no other.

    System User rather than Website User: `/api/resource/Supplier` is a desk
    resource, and a Website User is refused it outright — which is what the
    unprivileged token in spikes/frappe_capability/ demonstrates. Scoping is
    done with the role, not with the user type.
    """
    found = client.get(f"/api/resource/User/{USER}")
    if found.status_code >= 400:
        response = client.post(
            "/api/resource/User",
            json={
                "email": USER,
                "first_name": "Keplaria",
                "last_name": "Executor",
                "user_type": "System User",
                "enabled": 1,
                "send_welcome_email": 0,
                "roles": [{"role": ROLE}],
            },
        )
        response.raise_for_status()
        return "created"

    roles = {row["role"] for row in found.json()["data"].get("roles", [])}
    if roles != {ROLE}:
        response = client.put(
            f"/api/resource/User/{USER}", json={"roles": [{"role": ROLE}]}
        )
        response.raise_for_status()
        return f"roles corrected (was {sorted(roles)})"
    return "present"


def generate_keys(client) -> dict[str, str]:
    response = client.post(
        "/api/method/frappe.core.doctype.user.user.generate_keys",
        data={"user": USER},
    )
    response.raise_for_status()
    secret = response.json()["message"]["api_secret"]
    key = client.get(
        f"/api/resource/User/{USER}", params={"fields": json.dumps(["api_key"])}
    ).json()["data"]["api_key"]
    return {"api_key": key, "api_secret": secret}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--generate-keys",
        action="store_true",
        help="rotate the credential; deployed services break until Secret "
        "Manager and both Cloud Run revisions are updated",
    )
    args = parser.parse_args()

    with frappe_admin_client() as client:
        print(f"role   {ROLE}: {ensure_role(client)}")
        for doctype, state in sorted(ensure_permissions(client).items()):
            print(f"  perm {doctype}: {state}")
        for target, state in sorted(revoke_baseline_rights(client).items()):
            print(f"  base {target}: {state}")
        print(f"user   {USER}: {ensure_user(client)}")
        if args.generate_keys:
            keys = generate_keys(client)
            print(
                "\nNew credential. It is shown once. Put it in .env.secrets as\n"
                "FRAPPE_API_KEY / FRAPPE_API_SECRET, add both to Secret Manager,\n"
                "then create new keplaria-ingress and keplaria-review revisions.\n"
            )
            print(json.dumps(keys, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
