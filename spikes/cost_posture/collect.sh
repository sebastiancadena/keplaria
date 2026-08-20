#!/usr/bin/env bash
# Collect the observed cost posture for the judging window.
#
# Everything here is DISCOVERED, not hardcoded: budgets are found by listing the
# billing account, the net figure is read from the most recent kill-switch
# notification in Cloud Logging, and the gross figure is pulled from the
# observer subscription. The only manual input is the credit balance, which has
# no API — read it from the console (Billing -> Credits) and pass it in.
#
#   bash spikes/cost_posture/collect.sh [--credit-remaining USD --credit-expiry YYYY-MM-DD]
#
# Writes spikes/cost_posture/evidence.json.

set -euo pipefail

PROJECT="${PROJECT:-keplaria}"
REGION="${REGION:-us-central1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CREDIT_REMAINING=""
CREDIT_EXPIRY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --credit-remaining) CREDIT_REMAINING="$2"; shift 2 ;;
    --credit-expiry)    CREDIT_EXPIRY="$2";    shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "== discovering billing account =="
BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT" \
  --format="value(billingAccountName)" | sed 's|billingAccounts/||')"
[[ -n "$BILLING_ACCOUNT" ]] || { echo "FAIL: project has no billing account" >&2; exit 1; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")"
echo "   billing account: $BILLING_ACCOUNT"
echo "   project number:  $PROJECT_NUMBER"

echo "== discovering budgets =="
# Every budget, with the credit treatment that decides whether it watches gross
# or net spend. A budget scoped to the wrong project reads 0.0 forever and can
# never fire, so the project filter is checked rather than assumed.
BUDGETS_JSON="$(gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" --format=json)"
BUDGETS_JSON="$BUDGETS_JSON" WANT="$PROJECT_NUMBER" python3 -c '
import os, json
want = os.environ["WANT"]
for b in json.loads(os.environ["BUDGETS_JSON"]):
    f = b.get("budgetFilter", {})
    projects = f.get("projects") or []
    scoped = (not projects) or any(p.endswith(want) for p in projects)
    print("   %-26s %8s %-20s scope_ok=%s" % (
        b.get("displayName"),
        b.get("amount", {}).get("specifiedAmount", {}).get("units", "?"),
        f.get("creditTypesTreatment", "?"),
        scoped))
' || true

echo "== net spend (after credits), newest kill-switch notification =="
# The kill-switch logs every budget notification it receives. Its cost figure is
# month-to-date NET of all credits, because its budget includes all credits.
NET_LINE="$(gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="billing-killswitch" AND textPayload:"cost"' \
  --project="$PROJECT" --freshness=7d --limit=1 --format="value(textPayload)" || true)"
NET_COST="$(sed -n 's/.*cost \([0-9.]*\).*/\1/p' <<<"$NET_LINE")"
echo "   $NET_LINE"

echo "== gross spend (before credits), observer subscription =="
# The observer budget excludes all credits and publishes to its own topic with
# no subscriber but this one, so pulling here cannot starve the kill switch.
GROSS_RAW="$(gcloud pubsub subscriptions pull \
  "projects/${PROJECT}/subscriptions/billing-observe-pull" \
  --project="$PROJECT" --limit=10 --format=json 2>/dev/null || echo '[]')"
GROSS_COST="$(python3 -c '
import sys, json, base64
rows = json.load(sys.stdin)
best = None
for r in rows:
    d = r.get("message", {}).get("data")
    if not d:
        continue
    p = json.loads(base64.b64decode(d).decode())
    best = p.get("costAmount", best)
print(best if best is not None else "")
' <<<"$GROSS_RAW")"
if [[ -n "$GROSS_COST" ]]; then
  echo "   gross month-to-date: \$$GROSS_COST"
else
  echo "   (no notification queued yet; a new budget joins the ~25min cycle within the hour)"
fi

echo "== standing resources =="
ENGINE="$(gcloud ai agent-engines list --project="$PROJECT" --region="$REGION" \
  --format="value(name)" 2>/dev/null | head -1 || true)"
if [[ -z "$ENGINE" ]]; then
  ENGINE="$(curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    "https://${REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/reasoningEngines" \
    | python3 -c 'import sys,json; r=json.load(sys.stdin).get("reasoningEngines",[]); print(r[0]["name"] if r else "")')"
fi
ENGINE_SPEC="{}"
if [[ -n "$ENGINE" ]]; then
  ENGINE_SPEC="$(curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    "https://${REGION}-aiplatform.googleapis.com/v1/${ENGINE}" \
    | python3 -c '
import sys, json
ds = json.load(sys.stdin).get("spec", {}).get("deploymentSpec", {})
print(json.dumps({k: v for k, v in ds.items() if k != "env"}))')"
  echo "   engine: $ENGINE_SPEC"
fi

# Cloud Run services that do NOT scale to zero are a standing cost; list any.
PINNED_RUN="$(gcloud run services list --project="$PROJECT" --region="$REGION" \
  --format="value(metadata.name,spec.template.metadata.annotations['autoscaling.knative.dev/minScale'])" \
  | awk 'NF>1 && $2>0 {print $1"="$2}' | paste -sd, - || true)"
echo "   cloud run pinned above zero: ${PINNED_RUN:-none}"

VM_JSON="$(gcloud compute instances list --project="$PROJECT" \
  --format="json(name,status,machineType.basename(),zone.basename())")"
DISK_JSON="$(gcloud compute disks list --project="$PROJECT" \
  --format="json(name,sizeGb,type.basename())")"
ADDR_JSON="$(gcloud compute addresses list --project="$PROJECT" \
  --format="json(name,address,addressType,status)")"

# Measured VM uptime over the last week, from start/stop operations, so the
# judging-window projection uses the observed duty cycle and not a guess.
UPTIME_HOURS="$(gcloud compute operations list --project="$PROJECT" \
  --filter="operationType:(start OR stop)" \
  --format="value(insertTime,operationType,targetLink.basename())" 2>/dev/null \
  | sort | python3 -c '
import sys, datetime, collections
ev = collections.defaultdict(list)
for line in sys.stdin:
    parts = line.split()
    if len(parts) < 3:
        continue
    ts, op, target = parts[0], parts[1], parts[2]
    try:
        t = datetime.datetime.fromisoformat(ts)
    except ValueError:
        continue
    ev[target].append((t, op))
out = {}
for target, rows in ev.items():
    total, open_at = datetime.timedelta(), None
    for t, op in rows:
        if op == "start" and open_at is None:
            open_at = t
        elif op == "stop" and open_at is not None:
            total += t - open_at
            open_at = None
    days = max(1, (rows[-1][0] - rows[0][0]).days)
    out[target] = round(total.total_seconds() / 3600 / days, 1)
import json; print(json.dumps(out))
' || echo '{}')"
echo "   measured vm uptime hours/day: $UPTIME_HOURS"

echo "== writing evidence =="
COMMIT="$(git -C "$HERE/../.." rev-parse --short HEAD)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

OUT_PATH="$HERE/evidence.json" \
NOW="$NOW" COMMIT="$COMMIT" \
BILLING_ACCOUNT="$BILLING_ACCOUNT" PROJECT_NUMBER="$PROJECT_NUMBER" \
NET_COST="${NET_COST:-}" GROSS_COST="${GROSS_COST:-}" \
CREDIT_REMAINING="$CREDIT_REMAINING" CREDIT_EXPIRY="$CREDIT_EXPIRY" \
BUDGETS_JSON="$BUDGETS_JSON" ENGINE_SPEC="$ENGINE_SPEC" \
PINNED_RUN="${PINNED_RUN:-none}" VM_JSON="$VM_JSON" DISK_JSON="$DISK_JSON" \
ADDR_JSON="$ADDR_JSON" UPTIME_HOURS="$UPTIME_HOURS" \
python3 -c '
import os, json

def num(name):
    v = os.environ.get(name, "").strip()
    return float(v) if v else None

def txt(name):
    v = os.environ.get(name, "").strip()
    return v or None

doc = {
  "spike": "cost_posture",
  "criterion": "the judging window running cost is measured, not estimated, and the alerting that guards it is known to watch the right project",
  "date": os.environ["NOW"],
  "commit": os.environ["COMMIT"],
  "billing_account": os.environ["BILLING_ACCOUNT"],
  "project_number": os.environ["PROJECT_NUMBER"],
  "net_cost_month_to_date_usd": num("NET_COST"),
  "net_cost_note": "month-to-date after all credits; this is what is actually owed. Read from the newest kill-switch notification.",
  "gross_cost_month_to_date_usd": num("GROSS_COST"),
  "gross_cost_note": "month-to-date before credits, from the keplaria-gross-observe budget. null means no notification had been published yet at collection time.",
  "credit_remaining_usd": num("CREDIT_REMAINING"),
  "credit_expiry": txt("CREDIT_EXPIRY"),
  "credit_note": "console-only (Billing -> Credits); no API exposes the balance. Pass it in with --credit-remaining/--credit-expiry.",
  "budgets": json.loads(os.environ["BUDGETS_JSON"]),
  "engine_deployment_spec": json.loads(os.environ["ENGINE_SPEC"]),
  "cloud_run_pinned_above_zero": os.environ["PINNED_RUN"],
  "instances": json.loads(os.environ["VM_JSON"]),
  "disks": json.loads(os.environ["DISK_JSON"]),
  "addresses": json.loads(os.environ["ADDR_JSON"]),
  "measured_vm_uptime_hours_per_day": json.loads(os.environ["UPTIME_HOURS"]),
}
with open(os.environ["OUT_PATH"], "w") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
    fh.write("\n")
'

echo "   wrote $HERE/evidence.json"
