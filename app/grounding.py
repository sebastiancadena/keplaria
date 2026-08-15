"""Independent check that a model's extracted values came from the document.

A schema is not grounding. A model can return a perfectly well-typed expiry
date it invented, and the Pydantic contract will accept it. This module is
the second half: every value must resolve to a verbatim span on a declared
page of the exact document we handed over.

Total — never raises. A malformed result is ungrounded, not an exception,
because this runs on the path to a side effect and must fail closed.

Reason codes emitted by validate():
- MALFORMED_RESULT: result structure is invalid
- CHECKSUM_MISMATCH: document checksum does not match derivative
- PAGE_OUT_OF_RANGE: page index out of bounds
- SPAN_NOT_FOUND: span does not appear in page
- VALUE_NOT_IN_SPAN: value does not appear in span (or for DATE_FIELDS, value
  does not match the single ISO date extracted from the span)
- CONFIDENCE_OUT_OF_RANGE: confidence outside [0, 1]
- VALUE_NOT_A_DATE: value is in span but is not a valid ISO date
- AMBIGUOUS_SPAN: span contains multiple distinct dates (DATE_FIELDS only)
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

DATE_FIELDS = frozenset({"certificate_expiry"})


class RedactedDerivative(BaseModel):
    """What the model is allowed to see: page text plus the document's identity."""

    checksum: str
    pages: list[str]


class GroundingVerdict(BaseModel):
    grounded: bool
    reason: str = ""
    field: str = ""


def _fail(reason: str, field: str = "") -> GroundingVerdict:
    return GroundingVerdict(grounded=False, reason=reason, field=field)


def _extract_iso_dates(text: str) -> list[str]:
    """Extract unique ISO 8601 date strings (YYYY-MM-DD) from text.

    Returns a deduplicated list. Repeated identical dates count as one, since
    certificates often restate their expiry date in multiple places.
    """
    return list(dict.fromkeys(re.findall(r'\d{4}-\d{2}-\d{2}', text)))


def validate(result: dict, derivative: RedactedDerivative) -> GroundingVerdict:
    """Check every field against the derivative. First failure wins."""
    if not isinstance(result, dict):
        return _fail("MALFORMED_RESULT")

    if result.get("document_checksum") != derivative.checksum:
        # The agent must declare the document it actually read; anything else
        # means the provenance chain is broken before we even look at values.
        return _fail("CHECKSUM_MISMATCH")

    fields = result.get("fields")
    if not isinstance(fields, list):
        return _fail("MALFORMED_RESULT")

    for entry in fields:
        if not isinstance(entry, dict):
            return _fail("MALFORMED_RESULT")

        name = entry.get("name")
        if not isinstance(name, str):
            name = ""
        span = entry.get("span")
        value = entry.get("value")
        page = entry.get("page")
        confidence = entry.get("confidence")

        if not isinstance(span, str) or not isinstance(value, str):
            return _fail("MALFORMED_RESULT", name)
        if isinstance(page, bool) or not isinstance(page, int):
            return _fail("MALFORMED_RESULT", name)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return _fail("MALFORMED_RESULT", name)
        if not 0.0 <= float(confidence) <= 1.0:
            return _fail("CONFIDENCE_OUT_OF_RANGE", name)
        if not 0 <= page < len(derivative.pages):
            return _fail("PAGE_OUT_OF_RANGE", name)
        if span not in derivative.pages[page]:
            return _fail("SPAN_NOT_FOUND", name)
        if value not in span:
            # The span is real but does not say what the value claims.
            return _fail("VALUE_NOT_IN_SPAN", name)
        if name in DATE_FIELDS:
            # For date fields, extract all distinct ISO dates from the span.
            # The span must contain exactly one distinct date, equal to value.
            dates_in_span = _extract_iso_dates(span)
            if len(dates_in_span) == 0:
                # Value is in span but contains no ISO dates.
                return _fail("VALUE_NOT_A_DATE", name)
            if len(dates_in_span) > 1:
                # Multiple distinct dates in span — ambiguous which is the value.
                return _fail("AMBIGUOUS_SPAN", name)
            # Exactly one date in span — it must match the claimed value.
            if dates_in_span[0] != value:
                return _fail("VALUE_NOT_IN_SPAN", name)
            # Verify the value is a valid ISO date.
            try:
                date.fromisoformat(value)
            except ValueError:
                return _fail("VALUE_NOT_A_DATE", name)

    return GroundingVerdict(grounded=True)
