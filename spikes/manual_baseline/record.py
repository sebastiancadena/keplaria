"""Time a human doing by hand what the deployed system does end to end.

Run:
    uv run --env-file .env python spikes/manual_baseline/record.py

Read protocol.md first. The short version: this is the denominator for
"manual steps eliminated", it is AUTHOR-TIMED and NOT practitioner-reviewed,
and that qualifier travels with every number derived from it.

WHY A RECORDER AND NOT A STOPWATCH
----------------------------------
Because the step list is the claim. A number typed into a strategy file is
an assertion; a number produced by walking a fixed, committed list of steps
that each name the system action they mirror is a measurement someone else
can repeat and dispute. `STEPS` below is that list, and `mirrors` is the
link to the system's own actions -- if an action is added to the executor
and nothing here mirrors it, the baseline is knowably stale rather than
quietly wrong.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
Three steps stop short of their side effect (`timed_only`): two certificate
uploads and the renewal send. Both create ERP rows -- `File` and
`Communication` -- that scripts/erp.py can neither see nor purge, and a File
linked to a Supplier can refuse the Supplier delete outright. The manual
work being measured is reading and typing, which happens before the button;
the network call is not labour. Recording them as fully performed would buy
a slightly larger number in exchange for unpurgeable residue on a live ERP
that has a recording session pointed at it.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent / "evidence.json"

PERFORMED = "performed"
TIMED_ONLY = "timed_only"

# (phase, description, mirrors, mode)
#
# `mirrors` names the system action or node that removes this step. It is the
# only thing tying the baseline to what the system actually does; keep it
# accurate or the denominator stops meaning anything.
STEPS: list[tuple[str, str, str, str]] = [
    ("onboarding", "Open the certificate and read it end to end", "parse_case", PERFORMED),
    ("onboarding", "Enter the supplier name in the ERP", "create_supplier", PERFORMED),
    ("onboarding", "Choose the supplier group", "create_supplier", PERFORMED),
    ("onboarding", "Choose the supplier type", "create_supplier", PERFORMED),
    ("onboarding", "Enter the country", "create_supplier", PERFORMED),
    ("onboarding", "Enter the contact email address", "create_supplier", PERFORMED),
    ("onboarding", "Save the supplier record", "create_supplier", PERFORMED),
    ("onboarding", "Find and copy the certificate expiry date", "validate_evidence", PERFORMED),
    ("onboarding", "Search the supplier name in the sanctions tool", "screen_supplier", PERFORMED),
    ("onboarding", "Read the candidates and judge each near-match", "compliance_agent", PERFORMED),
    ("onboarding", "Record the screening decision durably", "assess_risk", PERFORMED),
    ("onboarding", "Attach the certificate to the supplier", "attach_evidence", TIMED_ONLY),
    ("onboarding", "Diarise the renewal date", "lifecycle.decide", PERFORMED),
    ("renewal", "Notice the renewal is due and find the supplier", "lifecycle.decide", PERFORMED),
    ("renewal", "Compose the renewal request email", "request_renewal", TIMED_ONLY),
    ("renewal", "Notice the evidence is overdue", "lifecycle.decide", PERFORMED),
    ("renewal", "Put the supplier on hold and choose the hold type", "apply_hold", PERFORMED),
    ("renewal", "Read the replacement certificate and find the new expiry", "validate_evidence", PERFORMED),
    ("renewal", "Attach the replacement certificate", "attach_evidence", TIMED_ONLY),
    ("renewal", "Release the hold", "clear_hold", PERFORMED),
]

VALIDATION = "author-timed, not practitioner-reviewed"


def prompt(index: int, total: int, phase: str, text: str, mode: str) -> str:
    tag = "  [stop before the send/upload button]" if mode == TIMED_ONLY else ""
    print(f"\n[{index}/{total}] ({phase}) {text}{tag}")
    return input("      ENTER when done · 's' to skip · 'q' to abort: ").strip().lower()


def main() -> int:
    print(__doc__.split("WHY A RECORDER")[0].strip())
    print(f"\n{len(STEPS)} steps. Read protocol.md before starting if you have not.")
    print("Do the work for real. Do not rush and do not pad.\n")
    if input("Ready? ENTER to start, 'q' to abort: ").strip().lower() == "q":
        print("aborted, nothing written")
        return 1

    records: list[dict] = []
    started = time.time()

    for index, (phase, text, mirrors, mode) in enumerate(STEPS, start=1):
        t0 = time.time()
        answer = prompt(index, len(STEPS), phase, text, mode)
        elapsed = round(time.time() - t0, 1)

        if answer == "q":
            print("\naborted, nothing written")
            return 1

        skipped = answer == "s"
        records.append({
            "step": index,
            "phase": phase,
            "description": text,
            "mirrors": mirrors,
            "mode": mode,
            "seconds": None if skipped else elapsed,
            "skipped": skipped,
        })
        print(f"      {'skipped' if skipped else f'{elapsed}s'}")

    timed = [r for r in records if not r["skipped"]]
    skipped = [r for r in records if r["skipped"]]

    evidence = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if not skipped else "PASS_WITH_SKIPS",
        "what_this_is": (
            "A single person performing by hand, against the same inputs, the "
            "work the deployed system does for one supplier across one full "
            "lifecycle cycle. The denominator for 'manual steps eliminated'."
        ),
        "validation": VALIDATION,
        "caveat": (
            "No procurement or compliance practitioner reviewed this step list "
            "or the timings. This is a measurement, not a validation, and no "
            "claim derived from it may be described as practitioner-validated."
        ),
        "manual_steps": len(STEPS),
        "steps_timed": len(timed),
        "steps_skipped": len(skipped),
        "manual_seconds": round(sum(r["seconds"] for r in timed), 1),
        "wall_clock_seconds": round(time.time() - started, 1),
        "timed_only_steps": [
            r["step"] for r in records if r["mode"] == TIMED_ONLY
        ],
        "timed_only_note": (
            "Certificate uploads and the renewal send were timed up to the "
            "button and not executed: both create Communication/File rows that "
            "scripts/erp.py cannot see or purge, and a File linked to a "
            "Supplier can refuse the Supplier delete."
        ),
        "steps": records,
    }

    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"\n{'=' * 60}")
    print(f"{len(STEPS)} manual steps, {evidence['manual_seconds']}s timed"
          f"{f' ({len(skipped)} skipped)' if skipped else ''}")
    print(f"written to {EVIDENCE}")
    print("Commit it — gate evidence belongs in the repo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
