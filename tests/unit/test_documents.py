"""Resolving a document reference. The seam the real preprocessor replaces."""

import pytest

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
