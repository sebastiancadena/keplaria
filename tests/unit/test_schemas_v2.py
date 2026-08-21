"""Schema v2: the department dimension, and the v1 grandfather guarantee."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import CanonicalEvent

_BASE = {
    "event_id": "EVT-1",
    "case_id": "CASE-1",
    "event_type": "new_supplier_packet",
    "supplier": "Andes Foods",
}


def test_a_v2_event_requires_a_department():
    with pytest.raises(ValidationError, match="department"):
        CanonicalEvent(**_BASE, schema_version=2)


def test_a_v1_event_without_a_department_still_parses():
    event = CanonicalEvent(**_BASE)
    assert event.schema_version == 1
    assert event.department is None


def test_a_v1_event_with_a_department_is_honored():
    event = CanonicalEvent(**_BASE, department="compliance")
    assert event.department == "compliance"
