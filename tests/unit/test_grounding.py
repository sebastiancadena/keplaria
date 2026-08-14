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
    # A span that looks like it might contain a date (matches YYYY-MM-DD pattern)
    # but is not a valid date (month 13 does not exist).
    verdict = validate(
        _result(field={"value": "2027-13-45", "span": "Expiry: 2027-13-45"}),
        RedactedDerivative(checksum="abc123", pages=["Expiry: 2027-13-45"]),
    )

    assert verdict.grounded is False
    assert verdict.reason == "VALUE_NOT_A_DATE"


def test_a_span_with_exactly_one_date_that_matches_the_value_passes():
    # The strict date extraction: single date in span must equal value.
    verdict = validate(_result(), DERIVATIVE)
    assert verdict.grounded is True


def test_a_span_with_two_dates_is_rejected_as_ambiguous():
    # Multiple dates in the span is ambiguous evidence.
    verdict = validate(
        _result(field={"span": "From 2027-01-01 to 2027-12-31"}),
        RedactedDerivative(checksum="abc123", pages=["From 2027-01-01 to 2027-12-31"]),
    )
    assert verdict.grounded is False
    assert verdict.reason == "AMBIGUOUS_SPAN"
    assert verdict.field == "certificate_expiry"


def test_a_span_with_one_date_but_wrong_value_is_rejected():
    # The span has exactly one date, but it doesn't match the claimed value.
    verdict = validate(
        _result(field={"value": "2030-01-01", "span": "Expiry: 2027-01-01"}),
        RedactedDerivative(checksum="abc123", pages=["Expiry: 2027-01-01"]),
    )
    assert verdict.grounded is False
    assert verdict.reason == "VALUE_NOT_IN_SPAN"
    assert verdict.field == "certificate_expiry"


def test_confidence_outside_the_unit_interval_is_rejected():
    verdict = validate(_result(field={"confidence": 1.4}), DERIVATIVE)

    assert verdict.grounded is False
    assert verdict.reason == "CONFIDENCE_OUT_OF_RANGE"


def test_validate_never_raises_on_a_malformed_result():
    # Each payload must include the correct checksum to actually reach the entry
    # validation code (not short-circuit on CHECKSUM_MISMATCH).
    for broken in (
        {"document_checksum": "abc123"},  # missing fields
        {"document_checksum": "abc123", "fields": "not a list"},  # fields not list
        {"document_checksum": "abc123", "fields": [None]},  # entry not dict
        {"document_checksum": "abc123", "fields": [{"page": "one"}]},  # page wrong type
    ):
        assert validate(broken, DERIVATIVE).grounded is False


def test_non_string_name_as_integer_is_guarded():
    # name field must be guarded against non-string types before any use.
    # Without the guard, an integer name passed to _fail() would raise
    # ValidationError from Pydantic, since GroundingVerdict.field is typed str.
    # The guard coerces non-string names to "". Totality: no exception raised.
    verdict = validate(
        {"document_checksum": "abc123", "fields": [
            {"name": 42, "span": "Expiry: 2027-01-01", "value": "2027-01-01",
             "page": 0, "confidence": 1.5}  # invalid confidence to trigger error path
        ]},
        DERIVATIVE,
    )
    # Confidence validation fails, but name guard prevented a crash.
    assert verdict.grounded is False
    assert verdict.reason == "CONFIDENCE_OUT_OF_RANGE"
    assert verdict.field == ""  # name was coerced to "" when non-string


def test_unhashable_name_as_list_is_guarded():
    # name field must be guarded before frozenset membership test ("if name in DATE_FIELDS").
    # Without the guard, an unhashable type (list) would raise TypeError when the
    # frozenset membership test tries to hash the name.
    # The guard coerces non-string names to "". Totality: no exception raised.
    verdict = validate(
        {"document_checksum": "abc123", "fields": [
            {"name": ["x"], "span": "Expiry: 2027-01-01", "value": "2027-01-01",
             "page": 0, "confidence": -0.5}  # invalid confidence to trigger error path
        ]},
        DERIVATIVE,
    )
    # Confidence validation fails, but name guard prevented a TypeError crash.
    assert verdict.grounded is False
    assert verdict.reason == "CONFIDENCE_OUT_OF_RANGE"
    assert verdict.field == ""  # name was coerced to "" when non-string
