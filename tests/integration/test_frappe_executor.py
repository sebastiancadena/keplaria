"""Runs against the real Frappe Cloud site; the ERP is the system of record.

Run with: uv run --env-file .env pytest tests/integration/test_frappe_executor.py -v
"""

import os
import uuid

import pytest

from app.executor.frappe import create_or_update_supplier, frappe_client

pytestmark = pytest.mark.skipif(
    not os.environ.get("FRAPPE_API_KEY"),
    reason="FRAPPE_* credentials not in the environment",
)


@pytest.fixture(scope="module")
def client():
    with frappe_client() as c:
        yield c


def test_create_returns_the_deterministic_external_id(client):
    name = f"TEST Supplier {uuid.uuid4().hex[:8]}"

    result = create_or_update_supplier(client, name)

    assert result["external_id"] == name, "Supplier.name must equal supplier_name"
    assert result["created"] is True


def test_second_create_is_reported_as_already_existing(client):
    name = f"TEST Supplier {uuid.uuid4().hex[:8]}"
    create_or_update_supplier(client, name)

    result = create_or_update_supplier(client, name)

    assert result["external_id"] == name
    assert result["created"] is False, "a repeat create must not be a new record"
