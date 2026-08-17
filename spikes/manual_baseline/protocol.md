# Manual baseline protocol

The denominator for "manual steps eliminated". One person doing by hand,
against the same inputs, what the deployed system does end to end for a
single supplier through one full lifecycle cycle.

## What this baseline is, stated the way it must always be stated

**Author-timed, not practitioner-reviewed.** No procurement or compliance
practitioner reviewed this walkthrough or validated the step list. It is a
real measurement of a real person doing the work, and it is *not* the
practitioner-validated baseline originally planned. Every number derived
from it travels with that qualifier attached — `app/metrics.py` carries it
in `baseline_validation` for exactly that reason, so the label cannot be
separated from the figure downstream.

The rule this satisfies: anonymous or self-collected feedback is acceptable,
invented validation is not. A timed walkthrough is a measurement. A guess
would not have been.

## Rules

1. **Use the same inputs the judge run uses** — the same certificate
   fixture, the same supplier details, the same watchlist.
2. **Do the work, do not mime it.** Read the document to find each value.
   Type it. Click through the real ERP forms.
3. **Two steps stop short of their side effect**, and are marked
   `timed_only` in the evidence: sending the renewal email, and uploading
   the certificate attachment. Both create ERP rows (`Communication`,
   `File`) that `scripts/erp.py` cannot see or purge, and a `File` linked to
   a Supplier can refuse the Supplier delete outright. Compose the message
   and select the file, stop at the send/upload button, and record the time
   up to that point. The typing is the manual work; the network call is not.
4. **One supplier only.** Use a name that is not in the ERP and not one of
   the judge-run suppliers, so the record can be purged afterwards.
5. **Do not rush and do not pad.** A baseline you have an interest in is
   only worth recording if you record it honestly. If a step takes four
   seconds because you already know the answer, four seconds is the number.
6. If you get interrupted, press `s` to skip the step and note it. A skipped
   step is excluded from the elapsed total and still counted in the step
   count — better a hole you can see than a number you invented.

## The steps

Each mirrors work the deployed system performs. The mapping to the system's
own actions is in `record.py`'s `STEPS`, so the two cannot drift silently.

**Onboarding (mirrors `new_supplier_packet`)**

1. Open the certificate and read it end to end
2. Enter the supplier name in the ERP
3. Choose the supplier group
4. Choose the supplier type
5. Enter the country
6. Enter the contact email address
7. Save the supplier record
8. Find and copy the certificate expiry date
9. Open the sanctions screening tool and search the supplier name
10. Read the candidate list and judge each near-match
11. Record the screening decision somewhere durable
12. Attach the certificate to the supplier record *(timed_only — stop at upload)*
13. Diarise the renewal date

**Renewal (mirrors `renewal_due` → `evidence_overdue` → `certificate_received`)**

14. Notice the renewal is due and find the supplier again
15. Compose the renewal request email *(timed_only — stop at send)*
16. Notice the evidence is overdue
17. Put the supplier on hold and choose the hold type
18. Read the replacement certificate and find the new expiry
19. Attach the replacement certificate *(timed_only — stop at upload)*
20. Release the hold

## Running it

```bash
uv run --env-file .env python spikes/manual_baseline/record.py
```

Writes `spikes/manual_baseline/evidence.json`. Commit it — this is gate
evidence and belongs in the repo, never in a scratchpad.
