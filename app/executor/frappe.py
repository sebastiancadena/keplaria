"""Scoped ERP surface. Only the deterministic executor calls this module.

No agent receives these functions as tools. The supplier record is keyed by its
own name, so a repeated create collides natively in the ERP rather than relying
on a constraint the hosting plan may not provide.
"""

from __future__ import annotations

import json
import os

import httpx


class FrappeError(RuntimeError):
    """An ERP call failed in a way the caller must handle."""


def frappe_client() -> httpx.Client:
    """Authenticated client for the configured Frappe site."""
    site = os.environ["FRAPPE_SITE"].rstrip("/")
    key = os.environ["FRAPPE_API_KEY"]
    secret = os.environ["FRAPPE_API_SECRET"]
    return httpx.Client(
        base_url=site,
        headers={"Authorization": f"token {key}:{secret}"},
        timeout=30,
        follow_redirects=True,
    )


def leaf_supplier_group(client: httpx.Client) -> str:
    """A non-group Supplier Group, required on every Supplier record."""
    response = client.get(
        "/api/resource/Supplier Group",
        params={"filters": json.dumps([["is_group", "=", 0]]), "limit_page_length": 1},
    )
    response.raise_for_status()
    rows = response.json()["data"]
    return rows[0]["name"] if rows else "All Supplier Groups"


def create_supplier_if_absent(
    client: httpx.Client, supplier_name: str, country: str = "Colombia"
) -> dict:
    """Create the Supplier, treating a native duplicate as success.

    This does not update an existing record — a duplicate is reported as
    `created: False` with no reconciliation of any field against `payload`.
    The name reflects that: it is a create-once-if-absent operation, not an
    upsert.

    Returns the deterministic external ID and whether this call created it.
    """
    payload = {
        "supplier_name": supplier_name,
        "supplier_group": leaf_supplier_group(client),
        "supplier_type": "Company",
        "country": country,
    }
    response = client.post("/api/resource/Supplier", json=payload)

    if response.status_code in (409, 417):
        # Already present from an earlier attempt — the deterministic ID holds.
        return {"external_id": supplier_name, "created": False}

    if response.status_code >= 400:
        raise FrappeError(
            f"supplier create failed: HTTP {response.status_code} {response.text[:300]}"
        )

    created_name = response.json()["data"]["name"]
    if created_name != supplier_name:
        raise FrappeError(
            f"non-deterministic ID: ERP returned {created_name!r} for {supplier_name!r}"
        )
    return {"external_id": created_name, "created": True}


def set_supplier_hold(
    client: httpx.Client, supplier_name: str, hold_type: str = "All"
) -> dict:
    """Apply ERPNext's native supplier hold. Idempotent by construction.

    `on_hold` / `hold_type` / `release_date` are stock Supplier fields, so the
    hold is real, reversible ERP state visible in the native UI rather than
    something this system invents. Writing the same state twice is a no-op on
    the ERP side, which is what makes a redelivered command safe.
    """
    response = client.put(
        f"/api/resource/Supplier/{supplier_name}",
        json={"on_hold": 1, "hold_type": hold_type, "release_date": None},
    )
    if response.status_code >= 400:
        raise FrappeError(
            f"hold failed: HTTP {response.status_code} {response.text[:300]}"
        )
    return {"external_id": supplier_name, "created": True}


def clear_supplier_hold(client: httpx.Client, supplier_name: str) -> dict:
    """Release the hold. Idempotent for the same reason as set_supplier_hold."""
    response = client.put(
        f"/api/resource/Supplier/{supplier_name}",
        json={"on_hold": 0, "hold_type": "", "release_date": None},
    )
    if response.status_code >= 400:
        raise FrappeError(
            f"hold release failed: HTTP {response.status_code} {response.text[:300]}"
        )
    return {"external_id": supplier_name, "created": True}


def send_supplier_message(
    client: httpx.Client, supplier_name: str, subject: str, body: str
) -> dict:
    """Send outbound correspondence to the supplier through the ERP.

    Unlike the hold operations, this is NOT idempotent: the ERP does not
    deduplicate outbound mail, so nothing here prevents a second identical
    message from actually sending. The cycle-scoped command ledger is the
    only guard against a redelivered command sending twice — that is
    precisely why command IDs carry the cycle.

    Recipient resolution is deliberately strict: a Supplier with no
    email_id is an error, not a silently skipped send. A notice nobody
    receives that reports success is worse than a failure.
    """
    supplier = client.get(f"/api/resource/Supplier/{supplier_name}")
    if supplier.status_code >= 400:
        raise FrappeError(
            f"supplier lookup failed: HTTP {supplier.status_code} {supplier.text[:300]}"
        )
    try:
        recipient = (supplier.json()["data"] or {}).get("email_id")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FrappeError(
            f"supplier lookup returned an unexpected body: {exc}"
        ) from exc
    if not recipient:
        raise FrappeError(f"supplier {supplier_name!r} has no email_id to write to")

    response = client.post(
        "/api/resource/Communication",
        json={
            "communication_type": "Communication",
            "communication_medium": "Email",
            "sent_or_received": "Sent",
            "subject": subject,
            "content": body,
            "recipients": recipient,
            "reference_doctype": "Supplier",
            "reference_name": supplier_name,
            "send_email": 1,
        },
    )
    if response.status_code >= 400:
        raise FrappeError(
            f"message failed: HTTP {response.status_code} {response.text[:300]}"
        )
    try:
        external_id = response.json()["data"]["name"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FrappeError(f"message send returned an unexpected body: {exc}") from exc
    return {"external_id": external_id, "created": True}


def attach_evidence(
    client: httpx.Client, supplier_name: str, cycle: int, content: bytes
) -> dict:
    """Attach the cycle's certificate to the Supplier record.

    Made idempotent by a deterministic filename plus a check-then-create:
    the filename is derived from supplier + cycle, so a redelivered command
    finds the existing File and reports `created: False` rather than
    stacking duplicate attachments — the same shape as
    create_supplier_if_absent.

    Verification note (observed against the live site): `upload_file`
    returns a usable identifier synchronously, in
    `response.json()["message"]["name"]` (e.g. "5394f5ede9") -- so the
    executor never needs a follow-up lookup to get an external_id. But that
    identifier is NOT deterministic: it is an opaque random key, unrelated
    to `file_name` or any input, so it cannot be recomputed the way
    create_supplier_if_absent recomputes the Supplier name. That is why
    idempotency here is done by looking up the File by
    (attached_to_doctype, attached_to_name, file_name) BEFORE uploading,
    rather than by recomputing the id.

    Also observed live: this site's File.before_insert runs a server-side
    PDF content scan (pypdf) whenever file_type resolves to "PDF". Content
    that merely starts with "%PDF-1.4" but isn't a well-formed PDF stream
    makes that scan raise an unhandled 500, not a graceful rejection --
    callers must pass genuinely valid PDF bytes.
    """
    file_name = f"{supplier_name}-cert-c{cycle}.pdf"

    existing = client.get(
        "/api/resource/File",
        params={
            "filters": json.dumps(
                [
                    ["attached_to_doctype", "=", "Supplier"],
                    ["attached_to_name", "=", supplier_name],
                    ["file_name", "=", file_name],
                ]
            ),
            "limit_page_length": 1,
        },
    )
    if existing.status_code >= 400:
        raise FrappeError(
            f"existence check failed: HTTP {existing.status_code} {existing.text[:300]}"
        )
    try:
        existing_rows = existing.json()["data"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FrappeError(
            f"existence check returned an unexpected body: {exc}"
        ) from exc
    if existing_rows:
        return {"external_id": existing_rows[0]["name"], "created": False}

    response = client.post(
        "/api/method/upload_file",
        data={
            "doctype": "Supplier",
            "docname": supplier_name,
            "file_name": file_name,
            "is_private": 1,
        },
        files={"file": (file_name, content, "application/pdf")},
    )
    if response.status_code >= 400:
        raise FrappeError(
            f"attach failed: HTTP {response.status_code} {response.text[:300]}"
        )
    try:
        external_id = response.json()["message"]["name"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FrappeError(f"upload returned an unexpected body: {exc}") from exc
    return {"external_id": external_id, "created": True}
