"""Independent check that a model's extracted values came from the document.

A schema is not grounding. A model can return a perfectly well-typed expiry
date it invented, and the Pydantic contract will accept it. This module is
the second half: every value must resolve to a verbatim span on a declared
page of the exact document we handed over.

Total — never raises. A malformed result is ungrounded, not an exception,
because this runs on the path to a side effect and must fail closed.
"""

from __future__ import annotations

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

        name = entry.get("name") or ""
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
            try:
                date.fromisoformat(value)
            except ValueError:
                return _fail("VALUE_NOT_A_DATE", name)

    return GroundingVerdict(grounded=True)
