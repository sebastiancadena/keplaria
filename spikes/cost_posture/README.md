# Cost posture

What it costs to keep Keplaria running through the judging window, measured
rather than estimated — and a check that the budget alerting guarding it is
actually watching the right project.

## Why this exists

The project's running-cost line rested on a figure derived from published
rates and never once checked against the bill. That is the same failure mode as
an eval score nobody re-ran: it is only evidence if it was observed.

Observing it turned out to be harder than reading a console number, for two
reasons:

1. **There is no API for spend.** Cloud Billing exposes budgets and accounts,
   not costs. The only programmatic sources are a BigQuery billing export
   (forward-only — it never backfills) and the budget notifications a budget
   publishes to Pub/Sub.
2. **Every budget here includes credits.** A budget with
   `INCLUDE_ALL_CREDITS` reports spend *after* credits. While a credit covers
   the account, every such budget reports `0.0` — which is a true number and a
   useless one, because it says nothing about the burn rate underneath.

## How the numbers are obtained

- **Net** (what is actually owed) comes from the newest kill-switch budget
  notification in Cloud Logging. The kill switch logs every notification it
  receives, so the log doubles as a spend history.
- **Gross** (burn before credits) comes from `keplaria-gross-observe`, a budget
  with `EXCLUDE_ALL_CREDITS` publishing to its own topic, `billing-observe`.
  It has one pull subscription and no other subscriber, deliberately: pulling
  from the kill switch's own topic would consume messages the kill switch needs
  to see.
- **Credit balance and expiry** have no API at all. They are read from the
  console and passed in as arguments; the collector records them with a note
  saying where they came from.
- **Per-SKU attribution** comes from the BigQuery Detailed usage cost export in
  `keplaria:billing_export`. It only contains data from the day it was enabled
  onward.

## Running it

```bash
bash spikes/cost_posture/collect.sh \
  --credit-remaining 138.09 --credit-expiry 2026-11-04
```

Rewrites `evidence.json` in place. Everything except the credit figures is
discovered at run time — budgets are listed rather than named, the engine is
looked up rather than hardcoded, and VM duty cycle is computed from actual
start/stop operations. A hardcoded budget id would go stale the first time a
budget was recreated, and the resulting `0.0` would look exactly like good news.

## Reading the output

`gross_cost_month_to_date_usd` is `null` when no notification had been
published yet — a newly created budget takes up to about half an hour to join
the notification cycle. That is a "not yet", not a zero. `null` and `0.0` mean
very different things in this file and are never used interchangeably.

`scope_ok` in the console output is the check that matters most: a budget whose
project filter does not match reports `0.0` forever and can never fire. That
failure is silent and looks identical to being under budget.
