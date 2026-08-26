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

- [x] Address bar: **`keplaria.com`** wherever a URL is on screen — the front
      door and `/proof` both live there as of 2026-08-22, and neither carries a
      cloud project identifier. When the case console itself must be shown, use
      the **hash form** (`keplaria-console-bklu5jcdea-uc.a.run.app`), never the
      numeric form (`keplaria-console-584548214478.us-central1.run.app`), which
      carries the cloud project number. **Checked 2026-08-26 against the 41 audit frames:** every address bar in the take is the hash form (`keplaria-review-bklu5jcdea-uc.a.run.app`, `keplaria-console-bklu5jcdea-uc.a.run.app`) and the demo ERP site.
- [x] Signed-in Google account avatar / name is out of frame or the profile
      is a clean one. No avatar in any frame.
- [x] Bookmarks bar hidden. No other tabs with identifying titles. No bookmarks bar, no other tabs in any frame.
- [x] Notifications silenced at the OS level, not just "probably won't fire". None appeared in the take (checked frame by frame).
- [x] No extension badges, no autofill dropdowns. None in any frame.

### The terminal

- [x] Prompt shows no host or user string that identifies a person. **N/A: no terminal is on camera** (three browser panes only); the same holds for the three lines below.
- [x] No `.env` path, no `gcloud config list`, no command echoing a key.
- [x] Scrollback cleared before the take — the take starts on a blank screen.
- [x] The harness prints no bearer token or signed URL. Confirm by reading
      the actual output of the rehearsal, not by assuming.

### Ground Control and the review service

- [x] Approval queue shows the masked account (see fixed findings above). `s•••@gmail.com` at f001–f008.
- [x] No case on screen belongs to an entity outside the demo set — run
      `scripts/erp.py audit` and read the WARN lines, which exist precisely
      because a substring match cannot judge the deliberate near-miss. Audit run before the take; the take shows only the synthetic demo suppliers (frames f001–f023).

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
- [x] Supplier list is clean. As of 2026-08-22 it still holds
      `DLQ Sweep Probe SAS` and three older test suppliers; a judge reading
      the list sees debris. Purge or crop. **Accepted as-is 2026-08-26:** the take shows `DLQ Sweep Probe SAS` (the live record that proves `one_erp_write_after_retry`; deleting it destroys that proof, see CLAUDE.md) and four synthetic demo suppliers. All are synthetic names; a judge sees a small demo list, not debris.
- [x] Site URL in frame is the demo site, and nothing in the page footer
      names a real person or a paid account. `andina-foods.v.frappe.cloud`, cropped to the data region; no footer in frame.

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

- [x] Every number matches the frozen run. The script's figures are bound in
      `docs/proof/claims.toml`; run
      `uv run python scripts/claim_ledger.py --check` and require a clean
      result before recording, not after. `claim_ledger.py --check` clean on 2026-08-26 (28 claims: 21 verified, 2 awaiting copy, 5 read by a human, 0 stale).
- [x] The baseline qualifier — **author-timed, not practitioner-reviewed** —
      is legible on screen wherever the comparison appears, not only spoken. On screen under the hook counter (f001–f003) and spoken.
- [x] No claim on screen that the audit of the code did not support. The
      five struck ones: pixel redaction, scanned/multimodal document
      handling, an inbound email path, an ERP 503 retry probe, and "the
      agent refuses" the injection (the deterministic scan blocks it before
      any agent sees it). `tests/test_beats.py` forbids the five phrases in every narration beat; the cold watch (below) found none on screen.

### Audio

- [x] Narration names no person and no account. Checked in `script/beats/*.txt`.
- [x] If music is used at all, it is licence-clear and recorded in
      `THIRD_PARTY.md` on first use. ACE-Step cue, in `THIRD_PARTY.md`; narration is Google Cloud TTS, also listed.

## Sign-off

- [x] Watched end to end, full screen, with the checklist open. The user, 2026-08-26, on the Cloud TTS cut.
- [x] Watched a second time by a reviewer who did not record it, looking only
      for text on screen. A zero-context subagent read all 41 frames for text (section C of the cold watch, 2026-08-26): it listed every URL, id and name on screen; all are hash-form service URLs, synthetic suppliers, case ids, the public engine id and the trace id already cleared above. Nothing new.
- [x] Uploaded, set public, and the public URL opened from a logged-out
      browser — the day-18 review pass, not a submission-day task.

## Cold watch (2026-08-26, Cloud TTS cut; 205.97 s when watched, 202.77 s after beat 2f was fitted)

A zero-context subagent read the 41 audit frames and the narration text, with
no other access, and answered the spec's four rubric questions at 30 s and at
full length.

| Question | At 30 s | Full length |
|---|---|---|
| What friction is removed | Partial: "onboarding ends at the ERP record, Keplaria stays for months later"; the counter's numbers were not yet explained | Answered: chasing renewals, sending the renewal email, applying and clearing the purchasing hold, re-keying fields; one Approve click remains |
| What the architecture is, and who may write to the ERP | Partial: consoles and the "coordinator proposes, versioned policy decides" line; the writer not yet named | Answered: Triggers → Coordinator (proposes only) → Policy Gate (fleet.v1) → Specialists (no ERP credential) → Outbox with the one scoped executor → ERP; Ground Control holds the parked path; the executor is the only writer |
| Was there unedited live execution | Weak: the LIVE badge and the 00:00.0 clock had only just appeared | Answered: one continuous clock to 01:14.1, the ERP row count and status changing in the real ERP tab; the viewer noted the held end frame and the hook's cuts of the take as the two things that made it pause |
| What "the fleet" means | Unanswered (the word is not in the first 30 s, by design: mechanism first, term second) | Answered: the versioned catalog of three departments, the agents each may engage and the five ERP commands they may issue; a case is a payload carried through it for months |

What the same watch flagged, and what was done:

- **Fixed:** the diagram clip rendered Space Grotesk through a fallback face
  (visible kerning gaps: "Coord inator", "exe cutor"); the scene now
  registers the vendored fonts, as the video repo's scenes already did.
- **Fixed:** the callout "Simulated clock: renewal due, nothing to do yet"
  contradicted itself and the narration ("not yet due"); now "first check,
  renewal not yet due".
- **Not defects, verified on the frames:** the hook counter's label morphs
  from "by hand" to "of machine work" over 0.3 s at 7.4 s (one audit frame
  sampled inside the morph); the grey disc behind the orbit body is the
  brand's echo (guidelines §8); the "1" on the close card is Space Grotesk's
  flagged glyph; `keplaria.com /proof` lands at 205 s, after the last sampled
  frame; the end state before the clock starts is the hook's four cuts of the
  take, and the frozen clock at 01:14.1 is the held end frame under beat 2f.
- **Narration claims the picture does not show** (the renewal email, "five
  under real hold", the verbatim spans, the supplier's one-email side): these
  are the script's evidence-bound lines, each tied to `docs/proof/claims.toml`
  and to `/proof`; the video states them and the proof page carries them. No
  change.
