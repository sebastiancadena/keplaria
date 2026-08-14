"""Day-2 Frappe capability spike — ERP capability gate.

Proves, against the real Frappe Cloud site and BEFORE any plan purchase:
  1. API-token authentication works.
  2. Deterministic record IDs: Supplier name == supplier_name, and a duplicate
     create is rejected (native uniqueness).
  3. Supplier hold/release behavior (on_hold + hold_type set and cleared).
  4. Outbound email: queued via the API and actually reaches Sent status.
  5. RBAC over the API: a no-role bot identity gets Frappe's native 403.

Reads FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET from the environment.
Run: uv run --env-file .env python spikes/frappe_capability/spike.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

SITE = os.environ["FRAPPE_SITE"].rstrip("/")
AUTH = f"token {os.environ['FRAPPE_API_KEY']}:{os.environ['FRAPPE_API_SECRET']}"
NOTIFY_EMAIL = os.environ.get("SPIKE_NOTIFY_EMAIL", "sebastiancadena@gmail.com")
SUPPLIER_NAME = "SPIKE Deterministic Supplier"
BOT_EMAIL = "spike-bot@keplaria.example"

client = httpx.Client(
    base_url=SITE, headers={"Authorization": AUTH}, timeout=30, follow_redirects=True
)
results: dict[str, str] = {}


def run_check(name, fn):
    try:
        results[name] = fn()
    except Exception as e:  # noqa: BLE001 — spike reports, never crashes
        detail = getattr(e, "response", None)
        body = detail.text[:300] if detail is not None else ""
        results[name] = f"FAIL: {type(e).__name__}: {e} {body}"
    print(f"[{name}] {results[name]}", flush=True)


def check_auth() -> str:
    r = client.get("/api/method/frappe.auth.get_logged_user")
    r.raise_for_status()
    return f"PASS: authenticated as {r.json()['message']}"


def _leaf_supplier_group() -> str:
    r = client.get(
        "/api/resource/Supplier Group",
        params={
            "filters": json.dumps([["is_group", "=", 0]]),
            "limit_page_length": 1,
        },
    )
    r.raise_for_status()
    rows = r.json()["data"]
    return rows[0]["name"] if rows else "All Supplier Groups"


def check_deterministic_ids() -> str:
    payload = {
        "supplier_name": SUPPLIER_NAME,
        "supplier_group": _leaf_supplier_group(),
        "supplier_type": "Company",
        "country": "Colombia",
    }
    r = client.post("/api/resource/Supplier", json=payload)
    if r.status_code == 409:
        created_name = SUPPLIER_NAME  # left over from a previous spike run
    else:
        r.raise_for_status()
        created_name = r.json()["data"]["name"]
    if created_name != SUPPLIER_NAME:
        return f"FAIL: name {created_name!r} != supplier_name {SUPPLIER_NAME!r}"
    dup = client.post("/api/resource/Supplier", json=payload)
    if dup.status_code not in (409, 417):
        return f"FAIL: duplicate create returned {dup.status_code}, expected 409/417"
    return (
        f"PASS: name == supplier_name ({created_name!r}); "
        f"duplicate rejected with HTTP {dup.status_code}"
    )


def check_hold_release() -> str:
    url = f"/api/resource/Supplier/{SUPPLIER_NAME}"
    r = client.put(url, json={"on_hold": 1, "hold_type": "All"})
    r.raise_for_status()
    held = client.get(url).json()["data"]
    if not held["on_hold"] or held["hold_type"] != "All":
        return f"FAIL: hold not applied: {held['on_hold']=} {held['hold_type']=}"
    r = client.put(url, json={"on_hold": 0})
    r.raise_for_status()
    released = client.get(url).json()["data"]
    if released["on_hold"]:
        return "FAIL: hold not released"
    return "PASS: on_hold set with hold_type=All, then released, both verified"


def check_outbound_email() -> str:
    r = client.post(
        "/api/method/frappe.core.doctype.communication.email.make",
        json={
            "recipients": NOTIFY_EMAIL,
            "subject": "Keplaria day-2 spike: outbound email check",
            "content": "Automated capability check. Synthetic data only.",
            "send_email": 1,
            "communication_medium": "Email",
        },
    )
    r.raise_for_status()
    deadline = time.time() + 180
    last_status = "never-queued"
    while time.time() < deadline:
        q = client.get(
            "/api/resource/Email Queue",
            params={
                "order_by": "creation desc",
                "limit_page_length": 1,
                "fields": json.dumps(["name", "status"]),
            },
        )
        q.raise_for_status()
        rows = q.json()["data"]
        if rows:
            last_status = rows[0]["status"]
            if last_status == "Sent":
                return f"PASS: email Sent to {NOTIFY_EMAIL} (queue {rows[0]['name']})"
            if last_status == "Error":
                return "FAIL: Email Queue status=Error (no outgoing account?)"
        time.sleep(10)
    return f"PARTIAL: queued but status={last_status!r} after 180s (scheduler lag?)"


def check_rbac_403() -> str:
    user_payload = {
        "email": BOT_EMAIL,
        "first_name": "Spike Bot",
        "user_type": "System User",
        "send_welcome_email": 0,
        "roles": [],
    }
    r = client.post("/api/resource/User", json=user_payload)
    if r.status_code not in (200, 409):
        r.raise_for_status()
    k = client.post(
        "/api/method/frappe.core.doctype.user.user.generate_keys",
        params={"user": BOT_EMAIL},
    )
    k.raise_for_status()
    bot_secret = k.json()["message"]["api_secret"]
    bot_key = client.get(f"/api/resource/User/{BOT_EMAIL}").json()["data"]["api_key"]

    bot = httpx.Client(
        base_url=SITE,
        headers={"Authorization": f"token {bot_key}:{bot_secret}"},
        timeout=30,
    )
    who = bot.get("/api/method/frappe.auth.get_logged_user")
    if who.status_code != 200:
        return f"FAIL: bot token does not authenticate ({who.status_code})"
    denied = bot.get("/api/resource/Supplier")
    if denied.status_code != 403:
        return (
            f"FAIL: no-role bot got HTTP {denied.status_code} on Supplier, "
            "expected native 403"
        )
    return "PASS: bot token authenticates but Supplier access → native 403"


run_check("api_token_auth", check_auth)
run_check("deterministic_ids", check_deterministic_ids)
run_check("supplier_hold_release", check_hold_release)
run_check("outbound_email", check_outbound_email)
run_check("rbac_native_403", check_rbac_403)

failed = [k for k, v in results.items() if v.startswith("FAIL")]
partial = [k for k, v in results.items() if v.startswith("PARTIAL")]
verdict = "FAIL" if failed else ("PARTIAL" if partial else "PASS")
print(f"\n[spike] VERDICT: {verdict} failed={failed} partial={partial}", flush=True)

# Gate evidence goes in the repo, never stdout-only (see CLAUDE.md Maintenance).
# The notify address is redacted: evidence.json is committed to a public repo.
evidence = {
    "spike": "frappe_capability",
    "gate": "day-2 Frappe capability spike",
    "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "site": SITE,
    "results": {k: v.replace(NOTIFY_EMAIL, "<notify-email>") for k, v in results.items()},
    "verdict": verdict,
}
evidence_path = Path(__file__).parent / "evidence.json"
evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
print(f"[spike] evidence written to {evidence_path}", flush=True)
sys.exit(1 if failed else 0)
