"""Trace every public number to the run that produced it."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Claim:
    """One public number, and the evidence file that produces it."""

    id: str
    claim: str
    evidence: str = ""
    path: str = ""
    render: str = ""
    appears_in: tuple[str, ...] = ()
    qualifier: str = ""
    verify: str = "evidence"
    reason: str = ""
    length: bool = False
    words: bool = False


def load(path: Path) -> tuple[Claim, ...]:
    """Read the ledger. One entry per public number, in the order stated."""
    entries = tomllib.loads(Path(path).read_text())["claim"]
    return tuple(
        Claim(
            id=entry["id"],
            claim=entry["claim"],
            evidence=entry.get("evidence", ""),
            path=entry.get("path", ""),
            render=entry.get("render", ""),
            appears_in=tuple(entry.get("appears_in", ())),
            qualifier=entry.get("qualifier", ""),
            verify=entry.get("verify", "evidence"),
            length=entry.get("length", False),
            words=entry.get("words", False),
            reason=entry.get("reason", ""),
        )
        for entry in entries
    )


@dataclass(frozen=True)
class Result:
    """What one claim's citation says when it is actually re-read."""

    claim: Claim
    status: str
    expected: str = ""
    missing_from: tuple[str, ...] = ()
    detail: str = ""

    @property
    def ok(self) -> bool:
        """A run fails on a number stated wrongly, or one that lost its source."""
        return self.status not in ("mismatch", "no_evidence")


def check(claim: Claim, root: Path) -> Result:
    """Re-read the evidence, then look for the value in every surface that states it."""
    if claim.verify == "manual":
        return Result(claim=claim, status="manual", detail=claim.reason)
    try:
        expected = resolve(claim, root)
    except (FileNotFoundError, KeyError, IndexError) as gone:
        return Result(
            claim=claim,
            status="no_evidence",
            detail=f"{claim.evidence} no longer provides {claim.path} ({gone!r})",
        )
    missing = tuple(
        surface
        for surface in claim.appears_in
        if expected.lower() not in (root / surface).read_text().lower()
    )
    if not claim.appears_in:
        status = "pending"
    elif missing:
        status = "mismatch"
    else:
        status = "ok"
    return Result(
        claim=claim, status=status, expected=expected, missing_from=missing
    )


# Prose spells small counts out ("Nine contracts"); the evidence stores 9.
_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
)


def resolve(claim: Claim, root: Path) -> str:
    """Read the claim's evidence and render the value the way prose states it."""
    payload = json.loads((root / claim.evidence).read_text())
    value = payload
    for part in claim.path.split("."):
        value = value[int(part)] if part.isdigit() else value[part]
    if claim.length:
        value = len(value)
    if claim.words:
        value = _WORDS[int(value)]
    return claim.render.format(value)


_STATUS_LABEL = {
    "ok": "verified",
    "mismatch": "MISMATCH",
    "manual": "read by a human",
    "pending": "copy not written yet",
    "no_evidence": "EVIDENCE GONE",
}

_HEADER = """# Claim → source ledger

Every number Keplaria states in public, and the run that produced it. This page
is **generated** by `scripts/claim_ledger.py` from `docs/proof/claims.toml`; it
is not written by hand, and the same command re-reads each evidence file rather
than trusting this table. A row marked MISMATCH means the prose and the
evidence disagree — the evidence is right.

| Claim | Value | Evidence | Stated in | Qualifier | Status |
|---|---|---|---|---|---|
"""


def render_markdown(claims: tuple[Claim, ...], root: Path) -> str:
    """Render the judge-facing page from the same data the check re-reads."""
    rows = []
    for claim in claims:
        result = check(claim, root)
        value = result.expected or result.detail or "—"
        evidence = (
            f"[`{claim.evidence}`]({_relative(claim.evidence)})"
            if claim.evidence
            else "—"
        )
        surfaces = ", ".join(f"`{s}`" for s in claim.appears_in) or "—"
        rows.append(
            f"| {claim.claim} | {value} | {evidence} | {surfaces} "
            f"| {claim.qualifier or '—'} | {_STATUS_LABEL[result.status]} |"
        )
    return _HEADER + "\n".join(rows) + "\n"


def _relative(evidence: str) -> str:
    """Link out of docs/proof/ to a path stated from the repository root."""
    return "../../" + evidence


_DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "docs" / "proof" / "claims.toml"


def main(argv: list[str] | None = None) -> int:
    """Re-read every citation, and optionally regenerate the page from it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="print every citation as it is re-read"
    )
    parser.add_argument("--render", action="store_true", help="regenerate claims.md")
    parser.add_argument("--ledger", default=str(_DEFAULT_LEDGER))
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    # The page belongs beside the data it is generated from: pointing --ledger
    # somewhere else must not leave the comparison aimed at the repository's
    # own page, which would compare two unrelated things.
    parser.add_argument("--page", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    ledger_path = Path(args.ledger)
    claims = load(ledger_path)
    results = [check(claim, root) for claim in claims]

    # Every citation is re-read either way -- a page rendered from unchecked
    # data is the failure this tool exists to prevent. --check only asks for
    # the per-claim lines.
    for result in results if args.check or not args.render else ():
        line = f"{_STATUS_LABEL[result.status]:>21}  {result.claim.id}"
        if result.expected:
            line += f"  = {result.expected}"
        if result.status == "mismatch":
            line += "  NOT FOUND IN " + ", ".join(result.missing_from)
        if result.status == "no_evidence":
            line += "  " + result.detail
        print(line)

    failing = [r for r in results if not r.ok]
    print(
        f"\n{len(results)} claims: "
        f"{sum(r.status == 'ok' for r in results)} verified, "
        f"{sum(r.status == 'pending' for r in results)} awaiting copy, "
        f"{sum(r.status == 'manual' for r in results)} read by a human, "
        f"{sum(r.status == 'mismatch' for r in results)} stale, "
        f"{sum(r.status == 'no_evidence' for r in results)} without evidence"
    )

    page = Path(args.page) if args.page else ledger_path.with_name("claims.md")
    rendered = render_markdown(claims, root)
    if args.render:
        page.write_text(rendered)
        print(f"wrote {page}")
    elif page.exists() and page.read_text() != rendered:
        # The page is judge-facing. A ledger edit that was never re-rendered
        # leaves a published table disagreeing with the check that vouches for
        # it, and every individual claim still verifies, so nothing else looks
        # wrong.
        print(f"OUT OF DATE  {page} no longer matches the ledger; re-run with --render")
        return 1

    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
