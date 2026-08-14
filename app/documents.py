"""Resolves a document reference to the redacted derivative a model may see.

Fixture-backed today. The signature is the seam: the real preprocessor
(quarantine fetch, pixel redaction, OCR, residual gate) implements this same
function later, and nothing upstream changes.

The checksum is computed from the page text rather than stored, so a fixture
cannot silently disagree with itself and make the grounding check vacuous.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.grounding import RedactedDerivative

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "documents"
FIXTURE_PREFIX = "fixture:"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class DocumentUnavailable(ValueError):
    """The reference cannot be resolved to a derivative this system may read."""


def checksum_for(pages: list[str]) -> str:
    return hashlib.sha256("\x00".join(pages).encode("utf-8")).hexdigest()


def load_document(ref: str) -> RedactedDerivative:
    """Resolve `ref`. Raises DocumentUnavailable for anything unsupported."""
    if not isinstance(ref, str) or not ref.startswith(FIXTURE_PREFIX):
        raise DocumentUnavailable(f"unsupported document reference scheme: {ref!r}")

    name = ref[len(FIXTURE_PREFIX):]
    if not _SAFE_NAME.match(name):
        # A reference arrives on an event; it is untrusted input, and this is
        # the only thing standing between it and an arbitrary file read.
        raise DocumentUnavailable(f"unsafe document name: {name!r}")

    path = FIXTURE_ROOT / f"{name}.json"
    try:
        payload = json.loads(path.read_text())
        pages = payload["pages"]
    except (OSError, ValueError, KeyError) as exc:
        raise DocumentUnavailable(f"cannot load {ref!r}: {exc}") from exc

    if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
        raise DocumentUnavailable(f"{ref!r}: pages must be a list of strings")

    return RedactedDerivative(checksum=checksum_for(pages), pages=pages)
