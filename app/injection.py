"""Deterministic scan for machine-directed instructions in document text.

This is a HEURISTIC over a representative fixture, not a general defence
against prompt injection, and every description of it — docstring, README,
narration — must say so. A rephrased payload passes. What is exact is the
other half: a document this module marks tainted is deterministically
incapable of producing an ERP write, because the graph never sends it to an
agent and the risk gate blocks the case.

Total — never raises. Same reason app.risk.assess is total: the serving
platform allows one concurrent query, so a raising scanner becomes retry
pressure rather than a decision. Malformed input is tainted, not an
exception; unreadable evidence fails closed.

`scan`'s signature is the seam. A later content-safety backend replaces this
body without touching the graph, the policy factor, or any test. That backend
cannot run inside the graph — the serving engine's network attachment has no
public internet egress — so it will live on the ingress side behind this same
signature.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# Patterns target text that addresses a machine READER rather than describing
# the entity the document is about. A certificate states facts; it never tells
# its reader what to report or what to conceal. Each id is stable and will be
# included in the persisted audit record by downstream tasks, making false
# positives diagnosable without quoting the payload back.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "IGNORE_PRIOR_INSTRUCTIONS",
        re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\b", re.I),
    ),
    (
        "ADDRESSES_AUTOMATED_READER",
        re.compile(
            r"\b(?:note|attention|instructions?)\s+to\s+(?:the\s+)?"
            r"(?:automated|ai|llm|agent|assistant|system)",
            re.I,
        ),
    ),
    (
        "DICTATES_OUTPUT",
        re.compile(r"(?:\w+_\w+)\s+you\s+must\s+(?:report|return|output|extract|state)\b", re.I),
    ),
    (
        "DEMANDS_CONCEALMENT",
        re.compile(
            r"\bdo\s+not\s+(?:mention|reveal|disclose|report)\b.*?"
            r"(?:in\s+your\s+)?(?:output|response|answer|return|extraction|analysis)\b",
            re.I,
        ),
    ),
    (
        "FORCES_CONFIDENCE",
        re.compile(r"\bconfidence\s+(?:of\s+)?1(?:\.0+)?\b", re.I),
    ),
)


class Finding(BaseModel):
    pattern_id: str
    page: int = -1     # -1 = not a location in the document
    offset: int = -1


class InjectionVerdict(BaseModel):
    tainted: bool
    findings: list[Finding] = []


def scan(pages) -> InjectionVerdict:
    """Scan page text for machine-directed instructions. Total — never raises."""
    if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
        return InjectionVerdict(tainted=True, findings=[Finding(pattern_id="MALFORMED_PAGES")])

    findings: list[Finding] = []
    for index, text in enumerate(pages):
        for pattern_id, pattern in PATTERNS:
            for match in pattern.finditer(text):
                findings.append(
                    Finding(pattern_id=pattern_id, page=index, offset=match.start())
                )

    return InjectionVerdict(tainted=bool(findings), findings=findings)
