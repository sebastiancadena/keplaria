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
#     (see docs/operations.md "Deploying" section).
#
# The keplaria-events-push subscription IS managed below, but only once
# keplaria-ingress is deployed (it needs the service URL as its push
# endpoint) — re-run this script after deploying to provision or repair it.
# Its retry policy matters for correctness, not just cost: with no minimum
# backoff, a single engine 429 (the Agent Runtime query quota allows only 1
# concurrent request) becomes a redelivery storm — ingress 503s, Pub/Sub
# redelivers near-instantly, claim_event honours it as a legitimate
# redispatch, another engine call burns quota, guaranteed 429, repeat. A
# minimum backoff is what turns that into a slow, quota-friendly retry
# instead.
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
echo "  that service is deployed — see docs/operations.md 'Deploying' section."

echo "== dead-letter topic =="
DEAD_TOPIC="keplaria-events-dead"
if gcloud pubsub topics describe "$DEAD_TOPIC" --project="$PROJECT" >/dev/null 2>&1; then
  echo "topic $DEAD_TOPIC already exists"
else
  gcloud pubsub topics create "$DEAD_TOPIC" --project="$PROJECT"
fi

# The Pub/Sub service agent — not our own identities — is what moves a message
# to the dead-letter topic and acks it on the source subscription. Without
# BOTH of these bindings dead-lettering silently does not happen: no error, no
# log line, messages just keep expiring at retention exactly as before.
echo "== dead-letter IAM (the silent-failure bindings) =="
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
PUBSUB_AGENT="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud pubsub topics add-iam-policy-binding "$DEAD_TOPIC" --project="$PROJECT" \
  --member="$PUBSUB_AGENT" --role=roles/pubsub.publisher >/dev/null
echo "  granted (or already held) pubsub.publisher on $DEAD_TOPIC"
gcloud pubsub subscriptions add-iam-policy-binding keplaria-events-push \
  --project="$PROJECT" --member="$PUBSUB_AGENT" \
  --role=roles/pubsub.subscriber >/dev/null
echo "  granted (or already held) pubsub.subscriber on keplaria-events-push"

echo "== push subscription =="
MIN_RETRY_DELAY="60s"
MAX_RETRY_DELAY="600s"
INGRESS_URL=$(gcloud run services describe keplaria-ingress --region=us-central1 \
  --project="$PROJECT" --format='value(status.url)' 2>/dev/null || true)
if [ -z "$INGRESS_URL" ]; then
  echo "keplaria-ingress not deployed yet — skipping subscription create/update."
  echo "Re-run this script after deploying it to provision keplaria-events-push."
elif gcloud pubsub subscriptions describe keplaria-events-push --project="$PROJECT" >/dev/null 2>&1; then
  echo "subscription keplaria-events-push already exists — ensuring retry policy is set"
  gcloud pubsub subscriptions update keplaria-events-push --project="$PROJECT" \
    --min-retry-delay="$MIN_RETRY_DELAY" --max-retry-delay="$MAX_RETRY_DELAY" \
    --dead-letter-topic="$DEAD_TOPIC" \
    --dead-letter-topic-project="$PROJECT" \
    --max-delivery-attempts=5 >/dev/null
else
  gcloud pubsub subscriptions create keplaria-events-push --project="$PROJECT" \
    --topic="$TOPIC" \
    --push-endpoint="${INGRESS_URL}/pubsub/push" \
    --push-auth-service-account="$PUSH_SA" \
    --min-retry-delay="$MIN_RETRY_DELAY" --max-retry-delay="$MAX_RETRY_DELAY" \
    --dead-letter-topic="$DEAD_TOPIC" \
    --dead-letter-topic-project="$PROJECT" \
    --max-delivery-attempts=5 \
    --ack-deadline=600
fi

echo "== dead-letter push subscription =="
if [ -z "$INGRESS_URL" ]; then
  echo "keplaria-ingress not deployed yet — skipping dead-letter subscription."
elif gcloud pubsub subscriptions describe keplaria-events-dead-push \
     --project="$PROJECT" >/dev/null 2>&1; then
  echo "subscription keplaria-events-dead-push already exists"
else
  # No retry policy and no dead-letter policy of its own: /pubsub/dead always
  # returns 200, so there is nothing to retry and nowhere further to escalate.
  gcloud pubsub subscriptions create keplaria-events-dead-push --project="$PROJECT" \
    --topic="$DEAD_TOPIC" \
    --push-endpoint="${INGRESS_URL}/pubsub/dead" \
    --push-auth-service-account="$PUSH_SA" \
    --ack-deadline=60
fi

echo "== sweeper identity and schedule =="
SWEEP_SA="keplaria-sweeper@${PROJECT}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$SWEEP_SA" --project="$PROJECT" >/dev/null 2>&1; then
  echo "sweeper SA already exists"
else
  gcloud iam service-accounts create keplaria-sweeper \
    --display-name="Keplaria scheduled command sweep" --project="$PROJECT"
fi

if [ -z "$INGRESS_URL" ]; then
  echo "keplaria-ingress not deployed yet — skipping run.invoker grant and job."
else
  gcloud run services add-iam-policy-binding keplaria-ingress \
    --region=us-central1 --project="$PROJECT" \
    --member="serviceAccount:${SWEEP_SA}" --role=roles/run.invoker >/dev/null
  echo "  granted (or already held) run.invoker on keplaria-ingress"

  if gcloud scheduler jobs describe keplaria-command-sweep \
       --location=us-central1 --project="$PROJECT" >/dev/null 2>&1; then
    echo "scheduler job keplaria-command-sweep already exists — updating"
    gcloud scheduler jobs update http keplaria-command-sweep \
      --location=us-central1 --project="$PROJECT" \
      --schedule="*/15 * * * *" \
      --uri="${INGRESS_URL}/admin/sweep" \
      --http-method=POST \
      --oidc-service-account-email="$SWEEP_SA" \
      --oidc-token-audience="$INGRESS_URL" \
      --attempt-deadline=300s >/dev/null
  else
    gcloud scheduler jobs create http keplaria-command-sweep \
      --location=us-central1 --project="$PROJECT" \
      --schedule="*/15 * * * *" \
      --uri="${INGRESS_URL}/admin/sweep" \
      --http-method=POST \
      --oidc-service-account-email="$SWEEP_SA" \
      --oidc-token-audience="$INGRESS_URL" \
      --attempt-deadline=300s
  fi
fi

echo "== done =="
echo "topic:      $(gcloud pubsub topics describe "$TOPIC" --project="$PROJECT" --format='value(name)')"
echo "ingress sa: $(gcloud iam service-accounts describe "$INGRESS_SA" --format='value(email)')"
echo "push sa:    $(gcloud iam service-accounts describe "$PUSH_SA" --format='value(email)')"
