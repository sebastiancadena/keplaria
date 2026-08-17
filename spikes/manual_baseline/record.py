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
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent / "evidence.json"

PERFORMED = "performed"
TIMED_ONLY = "timed_only"

SITE = "https://andina-foods.v.frappe.cloud"
SUPPLIER = "Empaques Sabana Norte SAS"
CONTACT_EMAIL = "empaques-sabana-norte-sas@example.com"

# Verified against the live site 2026-08-17 (ERPNext 16.32.0 / Frappe 16.31.0)
# by reading the Supplier DocType definition rather than recalling some other
# ERPNext. Two facts here are not guessable and were both wrong in the first
# draft of this protocol: Email ID is a FETCHED field (from
# supplier_primary_contact) and cannot be typed into, and the hold checkbox is
# labelled "Block Supplier", not "On Hold".
#
# (phase, description, mirrors, mode, hint)
#
# `mirrors` names the system action or node that removes this step. It is the
# only thing tying the baseline to what the system actually does; keep it
# accurate or the denominator stops meaning anything. `hint` is the navigation,
# printed with the prompt so this can be followed without a second window.
STEPS: list[tuple[str, str, str, str, str]] = [
    ("onboarding", "Read the certificate end to end", "parse_case", PERFORMED,
     "The certificate text is in protocol.md under 'Before you start'. Expiry is 2027-01-01."),
    ("onboarding", "Enter the supplier name", "create_supplier", PERFORMED,
     f"{SITE}/app/supplier/new  ->  Details tab  ->  Supplier Name = {SUPPLIER}"),
    ("onboarding", "Choose the supplier group", "create_supplier", PERFORMED,
     "Same form -> Supplier Group = Distributor (link field: type and pick)"),
    ("onboarding", "Choose the supplier type", "create_supplier", PERFORMED,
     "Same form -> Supplier Type = Company (dropdown: Company/Individual/Partnership)"),
    ("onboarding", "Enter the country", "create_supplier", PERFORMED,
     "Same form -> Country = Colombia (link field)"),
    ("onboarding", "Create a primary contact and set the email", "create_supplier", PERFORMED,
     "Ctrl+S first. Then Address & Contact tab -> Primary Address and Contact -> "
     "Supplier Primary Contact -> type 'Sabana Norte' -> 'Create a new Contact' -> "
     f"First Name 'Sabana Norte', Email '{CONTACT_EMAIL}' -> save dialog. "
     "Email ID is FETCHED from the contact and cannot be typed into directly."),
    ("onboarding", "Save the supplier record", "create_supplier", PERFORMED,
     "Ctrl+S on the supplier, with the contact now linked."),
    ("onboarding", "Find and copy the certificate expiry date", "validate_evidence", PERFORMED,
     "From the certificate text: 2027-01-01"),
    ("onboarding", "Search the supplier name in a sanctions tool", "screen_supplier", PERFORMED,
     f"https://www.opensanctions.org/search/ -> search '{SUPPLIER}'. "
     "NOTE: the system screens a private synthetic index with no browser UI, so "
     "this step is timed against the PUBLIC index. Same task, different dataset."),
    ("onboarding", "Read the candidates and judge each near-match", "compliance_agent", PERFORMED,
     "Decide for each result whether it plausibly refers to your supplier. "
     "Record the real time even if there are no hits."),
    ("onboarding", "Record the screening decision durably", "assess_risk", PERFORMED,
     "On the Supplier -> bottom timeline -> Comment -> write the decision and why -> save."),
    ("onboarding", "Attach the certificate to the supplier", "attach_evidence", TIMED_ONLY,
     "Supplier right sidebar -> Attachments -> Add file -> reach the file dialog -> CANCEL."),
    ("onboarding", "Diarise the renewal date", "lifecycle.decide", PERFORMED,
     f"{SITE}/app/todo/new -> Due Date 2027-01-01, description naming the supplier -> save."),
    ("renewal", "Notice the renewal is due and find the supplier", "lifecycle.decide", PERFORMED,
     f"{SITE}/app/supplier -> find and open '{SUPPLIER}'"),
    ("renewal", "Compose the renewal request email", "request_renewal", TIMED_ONLY,
     "Supplier -> bottom timeline -> New Email -> fill recipient, subject, message "
     "asking for the renewed certificate -> then DISCARD. Do not send."),
    ("renewal", "Notice the evidence is overdue", "lifecycle.decide", PERFORMED,
     "Check the ToDo against the expiry date; nothing has arrived."),
    ("renewal", "Put the supplier on hold and choose the hold type", "apply_hold", PERFORMED,
     "Supplier -> Settings tab -> 'Block Supplier' section -> tick 'Block Supplier' "
     "(this is the on_hold field), Hold Type = All, leave Release Date empty -> Ctrl+S."),
    ("renewal", "Read the replacement certificate and find the new expiry", "validate_evidence", PERFORMED,
     "Second certificate block in protocol.md. New expiry is 2028-01-01."),
    ("renewal", "Attach the replacement certificate", "attach_evidence", TIMED_ONLY,
     "Sidebar -> Attachments -> Add file -> reach the dialog -> CANCEL."),
    ("renewal", "Release the hold", "clear_hold", PERFORMED,
     "Supplier -> Settings tab -> untick 'Block Supplier' -> Ctrl+S."),
]

VALIDATION = "author-timed, not practitioner-reviewed"

SCREENING_NOTE = (
    "Step 9 was timed against the public OpenSanctions index, not the private "
    "synthetic watchlist the system screens: that index has no browser interface "
    "(the VM has no external IP and serves a JSON API only). The task is the "
    "same, the dataset is not."
)

CERTIFICATE = """
CERTIFICADO DE EXISTENCIA Y REPRESENTACION LEGAL
Comercializadora Andes Verde SAS
NIT: 900.123.456-7
Expiry: 2027-01-01
Issued by: Camara de Comercio (fictional test fixture)
"""

CLEANUP = f"""
CLEANUP -- do this now, so the ERP stays clean for recording:
  1. ToDo     {SITE}/app/todo        -> open the step-13 item -> menu -> Delete
  2. Supplier {SITE}/app/supplier    -> {SUPPLIER} -> menu -> Delete
  3. Contact  {SITE}/app/contact     -> 'Sabana Norte' -> menu -> Delete
     (if the Supplier delete is refused, delete the Contact first)
Steps 12, 15 and 19 stopped before their side effect, so they left nothing.
"""


def wrap(text: str, width: int = 74, indent: str = "      ") -> str:
    return "\n".join(textwrap.wrap(text, width=width, initial_indent=indent,
                                   subsequent_indent=indent))


def prompt(index: int, total: int, phase: str, text: str, mode: str, hint: str) -> str:
    tag = "   [TIMED ONLY - stop before the final button]" if mode == TIMED_ONLY else ""
    print(f"\n{'-' * 78}")
    print(f"[{index}/{total}] ({phase}) {text}{tag}")
    print(wrap(hint))
    print()
    return input("      ENTER when done · 's' to skip · 'q' to abort: ").strip().lower()


def main() -> int:
    print("=" * 78)
    print("MANUAL BASELINE -- author-timed, not practitioner-reviewed")
    print("=" * 78)
    print(wrap(
        "Timing one person doing by hand what the deployed system does end to "
        f"end. {len(STEPS)} steps; each prompt carries its own navigation, so you "
        "do not need to know ERPNext or keep protocol.md open.", indent=""))
    print()
    print("  Sign in first:  " + SITE + "/app/supplier")
    print("  Supplier to use: " + SUPPLIER)
    print("  Sanctions search: https://www.opensanctions.org/search/")
    print()
    print("  The certificate you are 'receiving' (expiry 2027-01-01):")
    for line in CERTIFICATE.strip().splitlines():
        print("      " + line)
    print()
    print(wrap("Do the work for real. Do not rush and do not pad -- a padded "
               "baseline is worse than none, because the whole point is that "
               "the number survives scrutiny.", indent="  "))
    print()
    if input("Ready? ENTER to start, 'q' to abort: ").strip().lower() == "q":
        print("aborted, nothing written")
        return 1

    records: list[dict] = []
    started = time.time()

    for index, (phase, text, mirrors, mode, hint) in enumerate(STEPS, start=1):
        t0 = time.time()
        answer = prompt(index, len(STEPS), phase, text, mode, hint)
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
            "navigation": hint,
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
        "screening_note": SCREENING_NOTE,
        "supplier_used": SUPPLIER,
        "erp": {"site": SITE, "erpnext": "16.32.0", "frappe": "16.31.0"},
        "steps": records,
    }

    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"\n{'=' * 78}")
    print(f"{len(STEPS)} manual steps, {evidence['manual_seconds']}s timed"
          f"{f' ({len(skipped)} skipped)' if skipped else ''}")
    print(f"written to {EVIDENCE}")
    print(CLEANUP)
    print("Then commit it -- gate evidence belongs in the repo, not a scratchpad:")
    print("  git add spikes/manual_baseline/evidence.json && \\")
    print('    git commit -m "test(baseline): record the author-timed manual walkthrough"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
