# Manual baseline protocol

The denominator for "manual steps eliminated". One person doing by hand,
against the same inputs, what the deployed system does end to end for a
single supplier through one full lifecycle cycle.

**You do not need to know ERPNext.** Every step below gives the exact URL or
the exact click path. Verified against this site on 2026-08-17 (ERPNext
16.32.0 / Frappe 16.31.0) by reading the live Supplier form definition, not
from memory of some other ERPNext.

## What this baseline is, stated the way it must always be stated

**Author-timed, not practitioner-reviewed.** No procurement or compliance
practitioner reviewed this walkthrough or validated the step list. It is a
real measurement of a real person doing the work, and it is *not* the
practitioner-validated baseline originally planned. Every number derived
from it travels with that qualifier attached — `app/metrics.py` carries it
in `baseline_validation` for exactly that reason, so the label cannot be
separated from the figure downstream.

The rule this satisfies: self-collected measurement is acceptable, invented
validation is not. A timed walkthrough is a measurement. A guess would not
have been.

## Before you start

**1. Open the ERP and sign in.**
<https://andina-foods.v.frappe.cloud/app/supplier>

You should see a Supplier list. If you land on a desk home instead, that URL
still works — paste it again once signed in.

**2. Use this supplier name, exactly:**

```text
Empaques Sabana Norte SAS
```

Checked 2026-08-17: not currently in the ERP, and not one of the two
judge-run suppliers. Using a judge-run name would corrupt a demo record;
using an existing name means the create step silently does nothing.

**3. Keep a second tab open** on the public sanctions search you will use in
step 9: <https://www.opensanctions.org/search/>

**4. The certificate.** This is the document you are "receiving". Read it
from here — there is no PDF to open:

```text
CERTIFICADO DE EXISTENCIA Y REPRESENTACION LEGAL
Comercializadora Andes Verde SAS
NIT: 900.123.456-7
Expiry: 2027-01-01
Issued by: Camara de Comercio (fictional test fixture)
```

And the replacement certificate, for step 18:

```text
CERTIFICADO DE EXISTENCIA Y REPRESENTACION LEGAL
Comercializadora Andes Verde SAS
NIT: 900.123.456-7
Expiry: 2028-01-01
Issued by: Camara de Comercio (fictional test fixture)
```

**5. Start the recorder** in a terminal, and follow its prompts. It shows one
step at a time and times each one:

```bash
uv run --env-file .env python spikes/manual_baseline/record.py
```

## Rules

1. **Do the work, do not mime it.** Read the document to find each value.
   Type it. Click through the real forms.
2. **Three steps stop short of their side effect** (marked `timed_only` in
   the evidence): the two certificate uploads and the renewal send. Each
   creates ERP rows — `Communication`, `File` — that `scripts/erp.py` cannot
   see or purge, and a `File` linked to a Supplier can refuse the Supplier
   delete outright. Fill the form, stop at the final button, cancel. The
   typing is the manual work; the network call is not.
3. **Do not rush and do not pad.** A baseline you have an interest in is only
   worth recording if you record it honestly. If a step takes four seconds
   because you already know the answer, four seconds is the number.
4. If you get interrupted, press `s` to skip. A skipped step is excluded from
   the elapsed total and still counted in the step count — better a hole you
   can see than a number you invented.
5. Do the cleanup at the end. It is three deletes.

## The steps

The recorder prompts these in order. Each mirrors work the deployed system
performs; the mapping to the system's own actions is in `record.py`'s
`STEPS`, so the two cannot drift silently.

### Onboarding — mirrors a `new_supplier_packet` event

**1. Read the certificate.** Read the first block in "Before you start"
above, end to end, as if it had just arrived.

**2–5. Create the supplier record.** Go to:
<https://andina-foods.v.frappe.cloud/app/supplier/new>

You land on the **Details** tab. Fill these four, which are four separate
timed steps — the recorder will prompt you one at a time:

| Step | Field | Value |
|---|---|---|
| 2 | Supplier Name | `Empaques Sabana Norte SAS` |
| 3 | Supplier Group | `Distributor` |
| 4 | Supplier Type | `Company` |
| 5 | Country | `Colombia` |

Supplier Group and Country are link fields — start typing and pick from the
dropdown. Supplier Type is a dropdown with three options.

**6. Create the primary contact and set the email.** This is the step that
looks like one field and is not. **Email ID on the Supplier form is a
fetched, non-editable field** — it displays whatever the linked primary
contact has, so you cannot type into it.

- **Save the supplier first: `Ctrl+S`.** Not optional. While the record is
  unsaved the form hides its whole contact area, and the Primary Contact
  field filters on the supplier's name — which does not exist yet.
- Go to the **Address & Contact** tab. The **Contacts** area is now visible;
  click **+ New Contact** there. (Equivalent: open
  <https://andina-foods.v.frappe.cloud/app/contact/new> directly.)
- Fill **First Name** `Sabana Norte` and **Email Address**
  `empaques-sabana-norte-sas@example.com`.
- **In the contact's `Links` table, add a row: Link Document Type =
  `Supplier`, Link Name = `Empaques Sabana Norte SAS`.** If you arrived via
  **+ New Contact** the row may already be filled in — check it, and add it
  if it is empty. Save the contact.
- Back on the supplier: **Address & Contact** tab → **Primary Address and
  Contact** section → **Primary Contact** field → pick `Sabana Norte`.
- `Ctrl+S` on the supplier. Email ID now shows the address.

> **Why the link row, and why this is not optional.** The Primary Contact
> field does not list every contact. It calls
> `erpnext.buying.doctype.supplier.supplier.get_supplier_primary` filtered by
> `supplier: doc.name`, which returns only contacts carrying a Dynamic Link
> row to *this* supplier. A contact created from the field's own
> **Create a new Contact** quick dialog is saved with an empty `links` table,
> so it is invisible to the field that created it — the dropdown comes back
> empty and the contact looks like it was never saved. **Corrected
> 2026-08-18** after that failure, by executing the field's query against the
> live site rather than reading the form definition, which shows the field
> but not the client-side filter applied to it.

**7. Save the supplier record.** `Ctrl+S`. (If you already saved during step
6, re-save after the contact is linked — that is the save being timed.)

**8. Find and copy the certificate expiry date.** From the certificate text,
locate the expiry and copy it: `2027-01-01`.

**9. Screen the supplier name.** In your second tab, search
`Empaques Sabana Norte SAS` at <https://www.opensanctions.org/search/>.

> **Read this before recording the time.** The system screens against a
> private synthetic 16-entity watchlist that has no public browser interface
> — the VM has no external IP and serves a JSON API only. So this step is
> timed against the *public* OpenSanctions index instead. The **task** is
> identical (type a name, get candidates, read them); the **dataset** is not.
> That difference is recorded in the evidence and affects this step's time
> only. Do not describe the manual screening as having used the same index.

**10. Read the candidates and judge each near-match.** Open the results and
decide, for each one, whether it plausibly refers to your supplier. Record
the time it actually takes, including when the answer is "no hits" — a clean
supplier being fast to clear is a real property of the work.

**11. Record the screening decision somewhere durable.** On the Supplier
record, scroll to the bottom timeline, click **Comment**, and write what you
decided and why. Save the comment.

**12. Attach the certificate — `timed_only`.** In the Supplier form's right
sidebar, find **Attachments** and click **Add file** / the paperclip. Get as
far as the file-selection dialog, then **Cancel**. Do not upload.

**13. Diarise the renewal.** Go to
<https://andina-foods.v.frappe.cloud/app/todo/new>, set the **Due Date** to
`2027-01-01` and write a description naming the supplier and the renewal.
Save.

### Renewal — mirrors `renewal_due` → `evidence_overdue` → `certificate_received`

**14. Notice the renewal is due and find the supplier again.** Go to
<https://andina-foods.v.frappe.cloud/app/supplier>, and find
`Empaques Sabana Norte SAS` in the list. Open it.

**15. Compose the renewal request — `timed_only`.** At the bottom of the
Supplier form, in the timeline, click **New Email**. Fill in the recipient,
a subject, and a message asking for the renewed certificate before the
expiry date. Then **close or discard the dialog. Do not send.**

**16. Notice the evidence is overdue.** Check your ToDo from step 13 against
the expiry date and register that nothing has arrived.

**17. Put the supplier on hold.** On the Supplier form, go to the
**Settings** tab → **Block Supplier** section:

- Tick **Block Supplier** (this is the `on_hold` field — it is *not* labelled
  "On Hold")
- Set **Hold Type** to `All` (options are All / Invoices / Payments)
- Leave **Release Date** empty
- `Ctrl+S`

**18. Read the replacement certificate and find the new expiry.** Read the
second certificate block in "Before you start". The new expiry is
`2028-01-01`.

**19. Attach the replacement certificate — `timed_only`.** Same as step 12:
sidebar → Attachments → Add file → reach the dialog → **Cancel**.

**20. Release the hold.** Settings tab → Block Supplier section → untick
**Block Supplier** → `Ctrl+S`.

## Cleanup

The recorder writes `evidence.json` and stops. Then remove what you created,
so the ERP stays clean for recording:

1. **The ToDo** — <https://andina-foods.v.frappe.cloud/app/todo>, open the one
   from step 13, menu (⋯) → Delete.
2. **The Supplier** — open `Empaques Sabana Norte SAS`, menu (⋯) → Delete.
   If it refuses because of a linked Contact, delete the Contact first.
3. **The Contact** — <https://andina-foods.v.frappe.cloud/app/contact>, find
   `Sabana Norte`, menu (⋯) → Delete.

The comment from step 11 goes with the Supplier. Nothing else was created,
because steps 12, 15 and 19 stopped before their side effect — which is the
whole reason they stop there.

**If an earlier attempt failed part-way**, check
<https://andina-foods.v.frappe.cloud/app/contact> for a leftover `Sabana
Norte` contact (Frappe suffixes repeats: `Sabana Norte-1`, `-2`) and delete it
before re-running. An orphan contact is harmless to the ERP but makes the next
run's naming confusing.

## Then

Commit the evidence — this is gate evidence and belongs in the repo, never in
a scratchpad:

```bash
git add spikes/manual_baseline/evidence.json && git commit -m "test(baseline): record the author-timed manual walkthrough"
```
