# Video audit — contest rule 12

Run this **before recording**, not after. Half of it changes what is on
screen, and a finding discovered after the take costs a re-shoot.

Rule 12 prohibits, in the submitted video: third-party advertising, logos and
trademarks; personal information; and anything that discloses credentials.
This project adds its own: no cloud project identifiers, no signed URLs, no
account emails.

Tick each line against the actual capture, not against intent.

## Findings already fixed in code

| Finding | Where it was | Status |
|---|---|---|
| The reviewer's account email rendered on the approval queue — the page the camera is pointed at during the approval beat | `/review`, "Signed in as …" | **FIXED.** `console/review.mask_account` renders `s•••@gmail.com`; the raw subject still goes to the audit trail, which is the one place it must stay. Page-level test, mutation-checked. |
| The public case page shows no account at all | `console/projection.py` | Already safe by design — the actor is withheld from the public projection, and its module docstring says so. |
| Supplier contact addresses | ERPNext supplier records | Already safe — `app/lifecycle._synthetic_email` issues `<slug>@example.com`, a reserved domain. No real address exists in the demo data. |

## Screen-by-screen checklist

### Browser chrome — the most likely leak, and the least watched

- [ ] Address bar: **`keplaria.com`** wherever a URL is on screen — the front
      door and `/proof` both live there as of 2026-08-22, and neither carries a
      cloud project identifier. When the case console itself must be shown, use
      the **hash form** (`keplaria-console-bklu5jcdea-uc.a.run.app`), never the
      numeric form (`keplaria-console-584548214478.us-central1.run.app`), which
      carries the cloud project number.
- [ ] Signed-in Google account avatar / name is out of frame or the profile
      is a clean one.
- [ ] Bookmarks bar hidden. No other tabs with identifying titles.
- [ ] Notifications silenced at the OS level, not just "probably won't fire".
- [ ] No extension badges, no autofill dropdowns.

### The terminal

- [ ] Prompt shows no host or user string that identifies a person.
- [ ] No `.env` path, no `gcloud config list`, no command echoing a key.
- [ ] Scrollback cleared before the take — the take starts on a blank screen.
- [ ] The harness prints no bearer token or signed URL. Confirm by reading
      the actual output of the rehearsal, not by assuming.

### Ground Control and the review service

- [ ] Approval queue shows the masked account (see fixed findings above).
- [ ] No case on screen belongs to an entity outside the demo set — run
      `scripts/erp.py audit` and read the WARN lines, which exist precisely
      because a substring match cannot judge the deliberate near-miss.

### ERPNext

- [ ] **Decide and record the trademark call.** The ERPNext UI carries Frappe
      and ERPNext marks. The stack is disclosed in `THIRD_PARTY.md` and the
      product is legitimately used, so showing it is ordinary
      technology-stack depiction rather than third-party advertising — but
      rule 12 lists trademarks explicitly, so the call belongs in writing
      before the take, not in an argument after judging. Alternative if the
      call goes the other way: show the supplier record cropped to the data
      region, with no masthead.
- [ ] Supplier list is clean. As of 2026-08-22 it still holds
      `DLQ Sweep Probe SAS` and three older test suppliers; a judge reading
      the list sees debris. Purge or crop.
- [ ] Site URL in frame is the demo site, and nothing in the page footer
      names a real person or a paid account.

### The scoreboard and overlays

- [ ] Every number matches the frozen run. The script's figures are bound in
      `docs/proof/claims.toml`; run
      `uv run python scripts/claim_ledger.py --check` and require a clean
      result before recording, not after.
- [ ] The baseline qualifier — **author-timed, not practitioner-reviewed** —
      is legible on screen wherever the comparison appears, not only spoken.
- [ ] No claim on screen that the audit of the code did not support. The
      three struck ones: pixel redaction, scanned/multimodal document
      handling, and an inbound email path.

### Audio

- [ ] Narration names no person and no account.
- [ ] If music is used at all, it is licence-clear and recorded in
      `THIRD_PARTY.md` on first use.

## Sign-off

- [ ] Watched end to end, full screen, with the checklist open.
- [ ] Watched a second time by a reviewer who did not record it, looking only
      for text on screen.
- [ ] Uploaded, set public, and the public URL opened from a logged-out
      browser — the day-18 review pass, not a submission-day task.
