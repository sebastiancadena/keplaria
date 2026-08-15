"""Runs against the real Frappe Cloud site; the ERP is the system of record.

Run with: uv run --env-file .env pytest tests/integration/test_frappe_executor.py -v
"""

import os
import urllib.parse
import uuid

import pytest

from app.executor.frappe import (
    FrappeError,
    attach_evidence,
    clear_supplier_hold,
    create_supplier_if_absent,
    frappe_client,
    send_supplier_message,
    set_supplier_hold,
)
from app.executor.frappe import PLACEHOLDER_CERTIFICATE_PDF as _MINIMAL_VALID_PDF

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("FRAPPE_API_KEY"),
        reason="FRAPPE_* credentials not in the environment",
    ),
]


@pytest.fixture(scope="module")
def client():
    with frappe_client() as c:
        yield c


@pytest.fixture
def supplier(client):
    """A fresh, uniquely-named test Supplier with an email_id set.

    Named with the same 'TEST Supplier' prefix the existing tests and
    scripts/erp.py already recognise for audit/purge, rather than a bare
    'TEST-' prefix, so this fixture's records are covered by the same
    cleanup tooling. Teardown best-effort deletes the record; if a live
    Communication or File still references it, the delete is left to a
    human via `scripts/erp.py purge --test-suppliers --yes`, and that is
    reported rather than silently swallowed.
    """
    name = f"TEST Supplier {uuid.uuid4().hex[:8]}"
    create_supplier_if_absent(client, name)
    email_response = client.put(
        f"/api/resource/Supplier/{name}",
        json={"email_id": f"test-{uuid.uuid4().hex[:8]}@example.com"},
    )
    assert email_response.status_code < 400, (
        f"could not set email_id on test supplier: HTTP "
        f"{email_response.status_code} {email_response.text[:300]}"
    )

    yield name

    quoted = urllib.parse.quote(name, safe="")
    delete_response = client.delete(f"/api/resource/Supplier/{quoted}")
    if delete_response.status_code not in (200, 202):
        print(
            f"\nWARN: could not clean up test supplier {name!r}: HTTP "
            f"{delete_response.status_code} {delete_response.text[:300]} "
            "-- leaving it for scripts/erp.py purge --test-suppliers --yes"
        )


@pytest.fixture
def supplier_without_email(client):
    """A fresh test Supplier that deliberately has no email_id.

    create_supplier_if_absent never sets email_id, so this is simply the
    bare-create path -- no extra API call needed to leave it unset. Same
    best-effort delete-on-teardown as the `supplier` fixture.
    """
    name = f"TEST Supplier {uuid.uuid4().hex[:8]}"
    create_supplier_if_absent(client, name)

    yield name

    quoted = urllib.parse.quote(name, safe="")
    delete_response = client.delete(f"/api/resource/Supplier/{quoted}")
    if delete_response.status_code not in (200, 202):
        print(
            f"\nWARN: could not clean up test supplier {name!r}: HTTP "
            f"{delete_response.status_code} {delete_response.text[:300]} "
            "-- leaving it for scripts/erp.py purge --test-suppliers --yes"
        )


def test_create_returns_the_deterministic_external_id(client):
    name = f"TEST Supplier {uuid.uuid4().hex[:8]}"

    result = create_supplier_if_absent(client, name)

    assert result["external_id"] == name, "Supplier.name must equal supplier_name"
    assert result["created"] is True


def test_second_create_is_reported_as_already_existing(client):
    name = f"TEST Supplier {uuid.uuid4().hex[:8]}"
    create_supplier_if_absent(client, name)

    result = create_supplier_if_absent(client, name)

    assert result["external_id"] == name
    assert result["created"] is False, "a repeat create must not be a new record"


def test_a_hold_can_be_applied_and_cleared(client, supplier):
    applied = set_supplier_hold(client, supplier)
    assert applied["external_id"] == supplier
    assert (
        client.get(f"/api/resource/Supplier/{supplier}").json()["data"]["on_hold"] == 1
    )

    set_supplier_hold(client, supplier)  # idempotent: same state written twice

    cleared = clear_supplier_hold(client, supplier)
    assert cleared["external_id"] == supplier
    assert (
        client.get(f"/api/resource/Supplier/{supplier}").json()["data"]["on_hold"] == 0
    )


def test_a_renewal_message_is_queued_for_delivery(client, supplier):
    result = send_supplier_message(
        client,
        supplier,
        "Certificate renewal required",
        "Your certificate expires on 2027-01-01.",
    )

    assert result["external_id"], "the Communication name is the external ID"
    assert result["created"] is True


def test_a_renewal_message_without_email_id_is_rejected(client, supplier_without_email):
    with pytest.raises(FrappeError, match="no email_id"):
        send_supplier_message(
            client,
            supplier_without_email,
            "Certificate renewal required",
            "Your certificate expires on 2027-01-01.",
        )


def test_attaching_evidence_twice_is_reported_as_not_created(client, supplier):
    first = attach_evidence(client, supplier, 1, _MINIMAL_VALID_PDF)
    second = attach_evidence(client, supplier, 1, _MINIMAL_VALID_PDF)

    assert first["created"] is True
    assert second["created"] is False, (
        "a redelivered attach must not create a second File record"
    )
    assert second["external_id"] == first["external_id"]
