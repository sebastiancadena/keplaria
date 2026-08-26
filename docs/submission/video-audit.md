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

- [x] **Trademark call, recorded 2026-08-25 (day 14): crop to the data
      region, no masthead.** The ERPNext UI carries Frappe and ERPNext marks.
      Showing them would be ordinary technology-stack depiction (the stack is
      disclosed in `THIRD_PARTY.md` and the product is legitimately used), but
      rule 12 lists trademarks explicitly and the crop costs nothing the video
      needs: the proof is the supplier row, the hold flag and the released
      hold, all of which live in the data region. So the ERP pane is framed on
      the list/record body only; ERPNext is named in narration and in the
      README, never shown as a logo. The call is written here so it is not
      argued after judging; reversing it means editing this line and the shot
      list in `video-script.md` together.
- [ ] Supplier list is clean. As of 2026-08-22 it still holds
      `DLQ Sweep Probe SAS` and three older test suppliers; a judge reading
      the list sees debris. Purge or crop.
- [ ] Site URL in frame is the demo site, and nothing in the page footer
      names a real person or a paid account.

### Cloud console captures (beat 3)

The rules ask the video to demonstrate that the backend runs on Google
Cloud, so beat 3 shows three console pages as stills. Captured 2026-08-25
(day 14) from the personal-profile Chrome at a 1920×1080 viewport, grabbed
from the X display (not the extension's scaled JPEG), filed in
`keplaria-video/media/gcp/` with the capture and masking scripts beside
them in `build/`. What each one had to lose before it passed:

- [x] **01 Agent Runtime deployment list.** The account avatar (a face)
      sits in the console's top bar on every page: masked with a patch of
      the same bar rows. **Never hover the avatar before a capture** — the
      tooltip prints the account name and email. The "Get started" promo
      card was dismissed. Resource name and identity columns show only
      the engine id (public in the README) and a truncated
      `...gserviceaccount.com`.
- [x] **02 Cloud Run services list.** The default "Deployed by" column
      prints the account email on every row: hidden through the column
      chooser. The service URL column is not displayed (it would carry the
      numeric project form). `billing-killswitch` stays in the list: it is
      a real service and hiding it would be a lie of omission.
- [x] **03 Cloud Trace, supplier one.** Trace `a0bf7262c679c2d9350698d7032da78c`,
      the `JR-A-E98AC9` onboarding invocation (coordinator → evidence →
      screening → compliance → `park_case`). Framed on the waterfall only:
      the span attribute panel prints `gcp.project_id` as the project
      NUMBER, so it is never opened on camera. The header shows the console
      timezone (`America/Indianapolis`); judged not identifying and left.
- [x] The extension used to drive the browser paints its own cursor into
      the page; it is parked on flat background and painted out. Check
      each frame for a stray arrow before use.

### The scoreboard and overlays

- [ ] Every number matches the frozen run. The script's figures are bound in
      `docs/proof/claims.toml`; run
      `uv run python scripts/claim_ledger.py --check` and require a clean
      result before recording, not after.
- [ ] The baseline qualifier — **author-timed, not practitioner-reviewed** —
      is legible on screen wherever the comparison appears, not only spoken.
- [ ] No claim on screen that the audit of the code did not support. The
      five struck ones: pixel redaction, scanned/multimodal document
      handling, an inbound email path, an ERP 503 retry probe, and "the
      agent refuses" the injection (the deterministic scan blocks it before
      any agent sees it).

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
