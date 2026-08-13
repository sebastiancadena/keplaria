#!/usr/bin/env bash
# Provisions the event spine: topic, identities, and IAM.
# Idempotent — every step tolerates the resource/binding already existing, so
# re-running after a partial failure (or just to confirm state) is harmless.
# "Already exists" is the ONLY tolerated failure: each create is preceded by
# a describe check, so creation is attempted only when the resource is
# actually missing. A genuine failure (bad permissions, wrong project, quota,
# a transient API error) is never mistaken for "already exists" — the create
# call runs with its stderr intact, and `set -e` stops the script on that
# real error instead of sailing into later steps with a misleading "ok".
#
# What this script does NOT do:
#   - Grant keplaria-pubsub-push@ roles/run.invoker on keplaria-ingress. That
#     binding targets a Cloud Run service that doesn't exist yet the first
#     time this script runs, so it happens after the service is deployed
#     (see the "Deploying" section of README.md).
#   - Create the keplaria-events-push subscription. It needs the deployed
#     service's URL as its push endpoint, so it is created in the same
#     post-deploy step as the run.invoker grant above.
set -euo pipefail

PROJECT="${PROJECT:-keplaria}"
TOPIC="keplaria-events"
INGRESS_SA="keplaria-ingress@${PROJECT}.iam.gserviceaccount.com"
PUSH_SA="keplaria-pubsub-push@${PROJECT}.iam.gserviceaccount.com"

echo "== topic =="
if gcloud pubsub topics describe "$TOPIC" --project="$PROJECT" >/dev/null 2>&1; then
  echo "topic $TOPIC already exists"
else
  gcloud pubsub topics create "$TOPIC" --project="$PROJECT"
fi

echo "== service accounts =="
if gcloud iam service-accounts describe "$INGRESS_SA" --project="$PROJECT" >/dev/null 2>&1; then
  echo "ingress SA already exists"
else
  gcloud iam service-accounts create keplaria-ingress \
    --display-name="Keplaria event ingress" --project="$PROJECT"
fi
if gcloud iam service-accounts describe "$PUSH_SA" --project="$PROJECT" >/dev/null 2>&1; then
  echo "push SA already exists"
else
  gcloud iam service-accounts create keplaria-pubsub-push \
    --display-name="Keplaria Pub/Sub push identity" --project="$PROJECT"
fi

echo "== ingress SA roles =="
for ROLE in roles/datastore.user roles/aiplatform.user roles/cloudtrace.agent; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${INGRESS_SA}" --role="$ROLE" --condition=None >/dev/null
  echo "  granted (or already held) $ROLE"
done

echo "== push SA =="
echo "  created above; roles/run.invoker on keplaria-ingress is granted after"
echo "  that service is deployed — see README.md 'Deploying' section."

echo "== done =="
echo "topic:      $(gcloud pubsub topics describe "$TOPIC" --project="$PROJECT" --format='value(name)')"
echo "ingress sa: $(gcloud iam service-accounts describe "$INGRESS_SA" --format='value(email)')"
echo "push sa:    $(gcloud iam service-accounts describe "$PUSH_SA" --format='value(email)')"
