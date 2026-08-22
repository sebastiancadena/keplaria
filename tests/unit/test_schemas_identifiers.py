"""Event identifiers, versions, and dates are validated at the edge.

An empty or path-like identifier would otherwise travel as far as the
Firestore boundary before failing — or worse, address a document it was
never meant to name — and an unrecognised forward schema_version must not
be interpreted under the current contracts. The ingress acks-and-drops a
rejected event (redelivery cannot fix a malformed payload), so every rule
here refuses at the door rather than deep in a workflow.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import SUPPORTED_SCHEMA_VERSIONS, CanonicalEvent

_BASE = {
    "event_id": "EVT-1",
    "case_id": "CASE-1",
    "event_type": "new_supplier_packet",
    "supplier": "Andes Foods",
}


def _event(**overrides) -> CanonicalEvent:
    return CanonicalEvent(**{**_BASE, **overrides})


def test_a_well_formed_event_still_parses():
    event = _event(department="procurement", effective_date="2026-01-05")
    assert event.event_id == "EVT-1"
    assert event.case_id == "CASE-1"


@pytest.mark.parametrize("field", ["event_id", "case_id"])
def test_an_empty_identifier_is_rejected(field):
    with pytest.raises(ValidationError, match=field):
        _event(**{field: ""})


@pytest.mark.parametrize("field", ["event_id", "case_id"])
def test_a_whitespace_only_identifier_is_rejected(field):
    with pytest.raises(ValidationError, match=field):
        _event(**{field: "   "})


@pytest.mark.parametrize("field", ["event_id", "case_id"])
def test_a_path_like_identifier_is_rejected(field):
    """A '/' in an identifier reads as a document path at the Firestore
    boundary — 'bad/path' must never get that far."""
    with pytest.raises(ValidationError, match=field):
        _event(**{field: "bad/path"})


@pytest.mark.parametrize("field", ["event_id", "case_id"])
@pytest.mark.parametrize("dot_name", [".", ".."])
def test_a_dot_identifier_is_rejected(field, dot_name):
    with pytest.raises(ValidationError, match=field):
        _event(**{field: dot_name})


@pytest.mark.parametrize("field", ["event_id", "case_id"])
def test_an_identifier_with_surrounding_whitespace_is_rejected(field):
    """' CASE-1' and 'CASE-1' must not become two different cases."""
    with pytest.raises(ValidationError, match=field):
        _event(**{field: " CASE-1"})


@pytest.mark.parametrize("field", ["event_id", "case_id"])
def test_an_unbounded_identifier_is_rejected(field):
    with pytest.raises(ValidationError, match=field):
        _event(**{field: "x" * 201})


def test_the_supported_schema_versions_are_enumerated():
    assert SUPPORTED_SCHEMA_VERSIONS == frozenset({1, 2})


@pytest.mark.parametrize("version", [0, -1, 3, 999])
def test_an_unsupported_schema_version_is_rejected(version):
    """A forward version is a contract this code has never seen; guessing
    that today's interpretation applies would be silent misreading, so the
    event refuses at the edge instead."""
    with pytest.raises(ValidationError, match="schema_version"):
        _event(schema_version=version, department="procurement")


def test_both_supported_versions_parse():
    assert _event(schema_version=1).schema_version == 1
    assert _event(schema_version=2, department="procurement").schema_version == 2


def test_a_malformed_effective_date_is_rejected():
    with pytest.raises(ValidationError, match="effective_date"):
        _event(effective_date="January 5th")


def test_an_absent_effective_date_is_still_fine():
    assert _event().effective_date is None


def test_an_unknown_field_is_rejected():
    """Every producer is first-party and emits exactly the declared fields;
    an unknown key is a producer bug or a tampered payload, not a feature."""
    with pytest.raises(ValidationError, match="surprise_field"):
        _event(surprise_field="x")
