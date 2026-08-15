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

Detection is a conjunction, not five independent regexes: text taints a page
only when a DIRECTIVE (an instruction addressed to the reader) and a
MACHINE-READER SIGNAL (something that only makes sense if the reader is an
automated system) occur in the *same sentence*. A certificate states facts
about the entity it describes; it never both instructs its reader and gives
that reader away as a machine in the same breath. Either half alone is
common, legitimate boilerplate — a confidentiality clause ("do not disclose
this document") or a holder obligation ("you must state your registration
number") is a directive with no machine signal, and stays clean.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# A note/attention/instructions block explicitly addressed to an automated
# reader is self-satisfying: framing content as "instructions to <reader>" is
# itself an instruction (a directive), and naming the reader as automated is
# itself the tell that this is not addressed to a human (a signal). It is the
# one pattern that appears in both lists below, on purpose.
_ADDRESSES_AUTOMATED_READER = re.compile(
    r"\b(?:note|attention|instructions?)\s+to\s+(?:the\s+)?(?:automated\s+)?"
    r"(?:ai|llm|agent|assistant|system|model|parser)s?\b",
    re.I,
)

# Directives: instructions addressed to the reader. None of these require a
# machine-reader signal on their own — "do not disclose this document" and
# "you must state your registration number" are ordinary human-directed
# boilerplate, and must stay clean until paired with a signal below.
DIRECTIVES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "DISREGARD_PRIOR_INSTRUCTIONS",
        re.compile(
            r"\b(?:ignore|disregard)\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions?\b",
            re.I,
        ),
    ),
    (
        "DEMANDS_CONCEALMENT",
        re.compile(r"\bdo\s+not\s+(?:mention|reveal|disclose|report)\b", re.I),
    ),
    (
        "DICTATES_OUTPUT",
        re.compile(r"\byou\s+must\s+(?:report|return|output|extract|state)\b", re.I),
    ),
    (
        "DICTATES_FIELD_REPORTING",
        re.compile(r"\breport\s+every\s+field\b", re.I),
    ),
    ("ADDRESSES_AUTOMATED_READER", _ADDRESSES_AUTOMATED_READER),
)

# Machine-reader signals: something that only makes sense if the reader is an
# automated system, not a human holder or auditor.
SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ADDRESSES_AUTOMATED_READER", _ADDRESSES_AUTOMATED_READER),
    (
        "REFERS_TO_PRIOR_INSTRUCTIONS",
        re.compile(r"\b(?:previous|prior|above|earlier)\s+instructions?\b", re.I),
    ),
    # Humans do not write field identifiers like `certificate_expiry` in
    # certificate prose; a snake_case token is a tell that the text is
    # steering a parser, not describing the entity the document is about.
    (
        "SNAKE_CASE_IDENTIFIER",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]*\b"),
    ),
    (
        "NUMERIC_CONFIDENCE_SCORE",
        re.compile(r"\bconfidence\s+(?:of\s+|score\s+)?\d+(?:\.\d+)?\b", re.I),
    ),
    # The preposition binding matters: "in your output" only makes sense
    # addressed to a system reporting its own generated output. "your
    # response to the audit questionnaire" is an ordinary reference to a
    # human's answer and must not match.
    (
        "POSSESSIVE_OUTPUT_REFERENCE",
        re.compile(r"\bin\s+your\s+(?:output|extraction|analysis|response|answer|return)\b", re.I),
    ),
)

# Sentence boundaries are found on a copy of the text with decimal points
# masked, so "confidence 1.0" is not split into "1" and "0" — but the
# sentences yielded are sliced from the original text, so content and offsets
# are exact. Three boundary rules, in order:
#
#   1. `.`, `!`, `?` always terminate a sentence.
#   2. A blank line (a paragraph break) always terminates a sentence.
#   3. A bare single line break terminates a sentence UNLESS the next line
#      begins with a lowercase letter.
#
# Rule 3 is the standard line-joining heuristic for extracted text, and this
# module's input is OCR/PDF-extracted page text where mid-sentence wrapping is
# ordinary rather than adversarial. A wrapped line continues in lowercase
# ("...the certificate_expiry\nyou must report...") and must stay one sentence,
# or an attacker gets an evasion for free from the extractor's own wrapping;
# a new clause starts capitalised ("...to third parties\nThe expiry_date is
# printed below") and must stay two, or an ordinary confidentiality clause
# followed by an unrelated field label quarantines a legitimate certificate.
# Joining is safe for matching because every directive/signal pattern joins its
# words with \s+, which spans the retained newline. The lowercase class covers
# the Latin-1 accented letters this corpus's Spanish prose uses.
_DECIMAL_POINT = re.compile(r"(?<=\d)\.(?=\d)")
_TERMINATOR = re.compile(r"[.!?]+|\n[ \t]*\n+|\n(?![ \t]*[a-zß-öø-ÿ])[ \t]*")


def _iter_sentences(text: str):
    """Yield (sentence_text, offset_in_text) for each sentence in text."""
    protected = _DECIMAL_POINT.sub("\x00", text)
    start = 0
    for terminator in _TERMINATOR.finditer(protected):
        yield text[start:terminator.start()], start
        start = terminator.end()
    yield text[start:], start


class Finding(BaseModel):
    pattern_id: str
    page: int = -1     # -1 = not a location in the document
    offset: int = -1


class InjectionVerdict(BaseModel):
    tainted: bool
    findings: list[Finding] = []


def scan(pages) -> InjectionVerdict:
    """Scan page text for machine-directed instructions. Total — never raises.

    A page taints only when a directive and a machine-reader signal are found
    in the same sentence. `Finding.pattern_id` is `"{directive}+{signal}"`:
    the directive is whichever directive pattern matched that sentence, but
    the signal half is always the first hit in `SIGNALS` iteration order —
    not necessarily the signal that best justifies the match when more than
    one signal pattern fires in the same sentence.

    `MALFORMED_PAGES` below is unreachable from the production path: by the
    time `scan` can be called from `load_case_state`, app/documents.py:58 has
    already rejected any document whose pages are not `list[str]`. It exists
    as a totality guarantee for this function's own contract as a seam, not
    because a caller is known to trigger it.
    """
    if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
        return InjectionVerdict(tainted=True, findings=[Finding(pattern_id="MALFORMED_PAGES")])

    findings: list[Finding] = []
    for index, text in enumerate(pages):
        for sentence, sentence_offset in _iter_sentences(text):
            directive_hits = [
                (pattern_id, match)
                for pattern_id, pattern in DIRECTIVES
                for match in pattern.finditer(sentence)
            ]
            if not directive_hits:
                continue

            signal_hits = [
                (pattern_id, match)
                for pattern_id, pattern in SIGNALS
                for match in pattern.finditer(sentence)
            ]
            if not signal_hits:
                continue

            signal_id = signal_hits[0][0]
            for directive_id, match in directive_hits:
                findings.append(
                    Finding(
                        pattern_id=f"{directive_id}+{signal_id}",
                        page=index,
                        offset=sentence_offset + match.start(),
                    )
                )

    return InjectionVerdict(tainted=bool(findings), findings=findings)
