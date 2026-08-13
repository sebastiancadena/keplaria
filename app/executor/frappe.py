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


def create_or_update_supplier(
    client: httpx.Client, supplier_name: str, country: str = "Colombia"
) -> dict:
    """Create the Supplier, treating a native duplicate as success.

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
