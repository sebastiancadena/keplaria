#!/usr/bin/env bash
# Push the yente stack files (plus the watchlist fixture) to the keplaria-yente
# VM's ~/yente-stack/, flattened — deploy.sh expects entities.ftm.json next to
# itself there. Run from the workstation; needs gcloud with IAP tunnel access.
set -euo pipefail

ZONE=us-central1-c
VM=keplaria-yente
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

gcloud compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap \
  --command 'mkdir -p ~/yente-stack'

gcloud compute scp --zone "$ZONE" --tunnel-through-iap \
  "$REPO_ROOT/infra/yente/deploy.sh" \
  "$REPO_ROOT/infra/yente/docker-compose.yml" \
  "$REPO_ROOT/infra/yente/manifest.yml" \
  "$REPO_ROOT/infra/yente/check.py" \
  "$REPO_ROOT/infra/yente/verify.sh" \
  "$REPO_ROOT/fixtures/watchlist/entities.ftm.json" \
  "$VM":yente-stack/

echo "pushed. Next, on the VM: bash ~/yente-stack/deploy.sh"
