"""Resolving a document reference. The seam the real preprocessor replaces."""

import json
from pathlib import Path

import pytest

from app import documents
from app.documents import DocumentUnavailable, load_document


def test_a_fixture_reference_loads_its_derivative():
    derivative = load_document("fixture:andes-verde-cert-2027")

    assert derivative.checksum
    assert "2027-01-01" in derivative.pages[0]


def test_an_unknown_scheme_is_refused():
    with pytest.raises(DocumentUnavailable):
        load_document("gs://some-bucket/object.pdf")


def test_a_traversing_name_is_refused():
    with pytest.raises(DocumentUnavailable):
        load_document("fixture:../../.env")


def test_a_missing_fixture_is_refused():
    with pytest.raises(DocumentUnavailable):
        load_document("fixture:no-such-document")


def test_a_bare_list_document_is_refused(tmp_path, monkeypatch):
    """JSON document that is a bare list (not an object) must raise DocumentUnavailable."""
    fixture_root = tmp_path / "documents"
    fixture_root.mkdir()
    (fixture_root / "bare-list.json").write_text(json.dumps([1, 2, 3]))
    monkeypatch.setattr(documents, "FIXTURE_ROOT", fixture_root)

    with pytest.raises(DocumentUnavailable):
        load_document("fixture:bare-list")


def test_a_bare_string_document_is_refused(tmp_path, monkeypatch):
    """JSON document that is a bare string (not an object) must raise DocumentUnavailable."""
    fixture_root = tmp_path / "documents"
    fixture_root.mkdir()
    (fixture_root / "bare-string.json").write_text(json.dumps("hello"))
    monkeypatch.setattr(documents, "FIXTURE_ROOT", fixture_root)

    with pytest.raises(DocumentUnavailable):
        load_document("fixture:bare-string")


def test_a_bare_number_document_is_refused(tmp_path, monkeypatch):
    """JSON document that is a bare number (not an object) must raise DocumentUnavailable."""
    fixture_root = tmp_path / "documents"
    fixture_root.mkdir()
    (fixture_root / "bare-number.json").write_text(json.dumps(42))
    monkeypatch.setattr(documents, "FIXTURE_ROOT", fixture_root)

    with pytest.raises(DocumentUnavailable):
        load_document("fixture:bare-number")
