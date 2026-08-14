"""Every extracted value must resolve to the document it claims to come from."""

from app.grounding import RedactedDerivative, validate

DERIVATIVE = RedactedDerivative(
    checksum="abc123",
    pages=["Certificate of good standing.\nExpiry: 2027-01-01\n"],
)


def _result(**overrides):
    field = {"name": "certificate_expiry", "value": "2027-01-01", "page": 0,
             "span": "Expiry: 2027-01-01", "confidence": 0.98}
    field.update(overrides.pop("field", {}))
    result = {"document_checksum": "abc123", "fields": [field]}
    result.update(overrides)
    return result


def test_a_faithful_extraction_is_grounded():
    assert validate(_result(), DERIVATIVE).grounded is True


def test_a_mismatched_checksum_is_rejected():
    verdict = validate(_result(document_checksum="deadbeef"), DERIVATIVE)

    assert verdict.grounded is False
    assert verdict.reason == "CHECKSUM_MISMATCH"


def test_a_span_absent_from_the_document_is_rejected():
    verdict = validate(_result(field={"span": "Expiry: 2099-01-01"}), DERIVATIVE)

    assert verdict.grounded is False
    assert verdict.reason == "SPAN_NOT_FOUND"


def test_a_page_out_of_range_is_rejected():
    verdict = validate(_result(field={"page": 7}), DERIVATIVE)

    assert verdict.grounded is False
    assert verdict.reason == "PAGE_OUT_OF_RANGE"


def test_a_schema_valid_but_unsupported_expiry_is_rejected():
    # The hallucination case: the span is real and present, but the claimed
    # value is not what the span says.
    verdict = validate(_result(field={"value": "2030-01-01"}), DERIVATIVE)

    assert verdict.grounded is False
    assert verdict.reason == "VALUE_NOT_IN_SPAN"
    assert verdict.field == "certificate_expiry"


def test_an_unparseable_expiry_is_rejected():
    verdict = validate(
        _result(field={"value": "next March", "span": "Expiry: next March"}),
        RedactedDerivative(checksum="abc123", pages=["Expiry: next March"]),
    )

    assert verdict.grounded is False
    assert verdict.reason == "VALUE_NOT_A_DATE"


def test_confidence_outside_the_unit_interval_is_rejected():
    verdict = validate(_result(field={"confidence": 1.4}), DERIVATIVE)

    assert verdict.grounded is False
    assert verdict.reason == "CONFIDENCE_OUT_OF_RANGE"


def test_validate_never_raises_on_a_malformed_result():
    for broken in ({}, {"fields": "not a list"}, {"fields": [None]},
                   {"document_checksum": "abc123", "fields": [{"page": "one"}]}):
        assert validate(broken, DERIVATIVE).grounded is False
