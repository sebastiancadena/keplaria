#!/usr/bin/env bash
# Environment doctor: verifies the local toolchain this project depends on.
# Safe to run anytime; read-only. Exits non-zero if any REQUIRED check fails.
set -uo pipefail
pass=0; fail=0; warn=0
ok()   { printf ' PASS  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf ' FAIL  %s\n' "$1"; fail=$((fail+1)); }
meh()  { printf ' WARN  %s\n' "$1"; warn=$((warn+1)); }

echo "== tool presence =="
for t in uv uvx gcloud node npx git; do
  command -v "$t" >/dev/null && ok "$t $($t --version 2>/dev/null | head -1)" || bad "$t not on PATH"
done
command -v agents-cli >/dev/null && ok "agents-cli $(agents-cli --version 2>/dev/null)" || bad "agents-cli missing (uv tool install google-agents-cli)"
command -v wrangler   >/dev/null && ok "wrangler $(wrangler --version 2>/dev/null)"     || meh "wrangler missing (needed for keplaria.com work)"
command -v gh         >/dev/null && ok "gh $(gh --version 2>/dev/null | head -1)"       || meh "gh missing"

echo "== auth =="
[ "$(gcloud config get-value project 2>/dev/null)" = "keplaria" ] \
  && ok "gcloud project = keplaria" || bad "gcloud project != keplaria"
gcloud auth application-default print-access-token >/dev/null 2>&1 \
  && ok "ADC token mints" || bad "ADC broken (do NOT fix by re-running gcloud init blindly)"
command -v wrangler >/dev/null && { wrangler whoami >/dev/null 2>&1 \
  && ok "wrangler authenticated" || meh "wrangler not authenticated"; }
command -v gh >/dev/null && { gh auth status >/dev/null 2>&1 \
  && ok "gh authenticated" || meh "gh not authenticated"; }

echo "== ERP maintenance tooling =="
[ -x scripts/erp.py ] \
  && ok "scripts/erp.py present (suppliers | cases | audit | purge --yes)" \
  || meh "scripts/erp.py missing or not executable — ERP cleanup falls back to ad-hoc one-offs"
# The pre-recording question this answers: is a watchlist entity on record?
# An exact string search does NOT answer it — the ERP once held
# 'Comercializadora Andes Verde SAS' while the watchlist carried the same
# entity as 'Comercializadora Andes Verde S.A.S.', and a grep called it clean.
[ -f fixtures/watchlist/entities.ftm.json ] \
  && ok "watchlist fixture present (scripts/erp.py audit can run)" \
  || meh "watchlist fixture missing — 'erp.py audit' cannot check for sanctioned records"

echo "== judge-visibility (private planning layer must not leak) =="
# The leak vector is our own writing: specs, plans and subagent briefs drafted
# from strategy/ carry risk IDs, plan-step labels and private filenames into
# code and commit messages when copied verbatim. Caught in app/risk.py and in a
# commit message on 2026-08-14. This grep is the automated backstop.
LEAK_RE='flight plan|architecture-contracts|risk-register|gates-and-cut|scoring-constitution|execution-plan|demo-and-video|Ground Control|\bR[1-9][0-9]?\b'
# -I skips binary files; the risk-id alternative can match arbitrary bytes in
# vendored binaries. We only care about text leaks from our own writing.
leak_files=$(git grep -lEi -I "$LEAK_RE" -- ':!strategy' ':!scripts/doctor.sh' 2>/dev/null)
[ -z "$leak_files" ] \
  && ok "no private planning vocabulary in the tracked tree" \
  || bad "private planning vocabulary in tracked files: $(echo "$leak_files" | tr '\n' ' ')"
# Commit messages are covered by the same rule and were the harder miss.
leak_msgs=$(git log --format='%h %s%n%b' -n 40 | grep -nEi "$LEAK_RE" | head -3)
[ -z "$leak_msgs" ] \
  && ok "last 40 commit messages carry no private vocabulary" \
  || meh "private vocabulary in a recent commit message — squash before pushing: $(echo "$leak_msgs" | head -1)"

echo "== project =="
[ -d .venv ] && uv lock --check >/dev/null 2>&1 \
  && ok "uv.lock consistent with pyproject" || meh ".venv/lock drift (run: uv sync)"
# uv run pytest is a SUBSET (-m 'not live'). Report both halves so a test that
# silently landed in a live-marked file is visible rather than invisible.
if [ -d .venv ]; then
  sel=$(uv run pytest --collect-only -q 2>/dev/null | grep -oE '[0-9]+/[0-9]+ tests collected' | head -1)
  live=$(uv run pytest -m live --collect-only -q 2>/dev/null | grep -oE '^[0-9]+/[0-9]+ tests collected|[0-9]+ tests collected' | head -1)
  [ -n "$sel" ] && ok "default suite selection: $sel (live-only: ${live:-unknown})" \
    || meh "could not determine test selection split"
fi

echo "== cloud infra (read-only) =="
state=$(gcloud functions describe billing-killswitch --region=us-central1 --gen2 \
  --format='value(state,serviceConfig.environmentVariables.DRY_RUN)' --project=keplaria 2>/dev/null)
echo "$state" | grep -q 'ACTIVE' && ok "billing kill switch deployed" || bad "billing kill switch not ACTIVE"
echo "$state" | grep -qi 'false' && ok "billing kill switch ARMED (DRY_RUN=false)" || meh "billing kill switch in dry-run mode"
gcloud pubsub topics describe billing-killswitch --project=keplaria >/dev/null 2>&1 \
  && ok "billing-killswitch topic" || bad "billing-killswitch topic missing"
gcloud compute networks describe keplaria-vpc --project=keplaria >/dev/null 2>&1 \
  && ok "keplaria-vpc network" || bad "keplaria-vpc missing"
yente_status=$(gcloud compute instances list --filter=name=keplaria-yente \
  --format='value(status)' --project=keplaria 2>/dev/null)
case "$yente_status" in
  RUNNING)    ok "keplaria-yente VM RUNNING" ;;
  TERMINATED) meh "keplaria-yente VM TERMINATED — nightly stop fired, no start schedule exists; start it (expect us-central1-c stockout retries)" ;;
  "")         meh "keplaria-yente VM not created (us-central1 stockout — retry loop?)" ;;
  *)          meh "keplaria-yente VM in state $yente_status" ;;
esac
gcloud compute firewall-rules describe keplaria-allow-internal --project=keplaria \
  --format='value(allowed[].map().firewall_rule().list())' 2>/dev/null | grep -q 'tcp:8000' \
  && ok "yente port 8000 open inside keplaria-vpc" || bad "keplaria-allow-internal missing tcp:8000 (PSC-I → yente will fail)"

echo "== Agent Runtime deploy preconditions =="
[ -f .gcloudignore ] && grep -q '^strategy$' .gcloudignore \
  && ok ".gcloudignore excludes the private strategy symlinks" \
  || bad ".gcloudignore missing/incomplete — a deploy would package strategy/ into the container"
# app/risk.py's DEFAULT_POLICY_PATH resolves to this file at runtime — the
# first runtime-required file outside app/. If .gcloudignore ever excludes
# policy/, load_policy() fails closed on every case (POLICY_UNAVAILABLE ->
# blocked): a silent, total onboarding outage.
[ -f policy/supplier_risk.v2.json ] \
  && python3 -c "import json; json.load(open('policy/supplier_risk.v2.json'))" >/dev/null 2>&1 \
  && ok "policy fixture exists and parses (policy/supplier_risk.v2.json)" \
  || bad "policy fixture missing or does not parse — every case would fail closed to POLICY_UNAVAILABLE/blocked"
grep -qE '^policy/?$' .gcloudignore \
  && bad ".gcloudignore excludes policy/ — the runtime-required fixture would not ship, every case fails closed" \
  || ok "policy/ is not excluded from the deploy package"
# .gcloudignore controls the Cloud Build source upload; it does not control
# what lands inside the image — only the Dockerfile's COPY list does that.
# policy/ and fixtures/ live outside app/ (see app/risk.py DEFAULT_POLICY_PATH,
# app/documents.py FIXTURE_ROOT), so an unmodified `COPY ./app ./app`-only
# Dockerfile ships neither: load_policy() fails closed to blocked, and
# load_document() raises DocumentUnavailable on every documented event,
# quarantining the case before screening ever runs. Caught live on 2026-08-14
# — the deployed engine quarantined a lifecycle harness run at step 1 with
# zero indication in Firestore beyond "DocumentUnavailable" on a trace span.
grep -qE '^\s*COPY\s+\./policy\s+\./policy\s*$' Dockerfile \
  && ok "Dockerfile copies policy/ into the image" \
  || bad "Dockerfile does not COPY ./policy ./policy — load_policy() fails closed on every deployed case"
grep -qE '^\s*COPY\s+\./fixtures\s+\./fixtures\s*$' Dockerfile \
  && ok "Dockerfile copies fixtures/ into the image" \
  || bad "Dockerfile does not COPY ./fixtures ./fixtures — load_document() raises DocumentUnavailable on every deployed case with a document_ref"
gcloud compute network-attachments describe keplaria-psc2 --region=us-central1 \
  --project=keplaria >/dev/null 2>&1 \
  && ok "network attachment keplaria-psc2" || bad "keplaria-psc2 missing (PSC-I → yente will fail)"
gcloud compute firewall-rules describe keplaria-allow-psc-to-yente --project=keplaria \
  --format='value(allowed[].map().firewall_rule().list())' 2>/dev/null | grep -q 'tcp:8000' \
  && ok "PSC subnet may reach yente on 8000" \
  || bad "keplaria-allow-psc-to-yente missing tcp:8000 — the attachment NIC lands in 10.10.1.0/24, which keplaria-allow-internal does NOT cover"
# The producer PATCHes the attachment, so networkAdmin (not just networkUser) is required.
sa_roles=$(gcloud projects get-iam-policy keplaria --flatten='bindings[].members' \
  --filter='bindings.members:service-584548214478@gcp-sa-aiplatform.iam.gserviceaccount.com' \
  --format='value(bindings.role)' 2>/dev/null)
echo "$sa_roles" | grep -q 'compute.networkAdmin' \
  && ok "aiplatform SA has compute.networkAdmin (networkAttachments.update)" \
  || bad "aiplatform SA lacks compute.networkAdmin — PSC deploys 403 on networkAttachments.update"
re_roles=$(gcloud projects get-iam-policy keplaria --flatten='bindings[].members' \
  --filter='bindings.members:service-584548214478@gcp-sa-aiplatform-re.iam.gserviceaccount.com' \
  --format='value(bindings.role)' 2>/dev/null)
echo "$re_roles" | grep -q 'compute.networkUser' \
  && ok "aiplatform-re SA has compute.networkUser" || bad "aiplatform-re SA lacks compute.networkUser"
gcloud services list --enabled --project=keplaria 2>/dev/null | grep -q 'cloudresourcemanager' \
  && ok "cloudresourcemanager API enabled" || bad "cloudresourcemanager disabled (Cloud Logging + OTel resource detector need it)"
# Base image and requires-python must agree or the lock installs into the wrong venv.
img=$(grep -oP '^FROM python:\K[0-9]+\.[0-9]+' Dockerfile 2>/dev/null | head -1)
req=$(grep -oP 'requires-python\s*=\s*">=\K[0-9]+\.[0-9]+' pyproject.toml 2>/dev/null | head -1)
[ -n "$img" ] && [ "$img" = "$req" ] \
  && ok "Dockerfile python:$img matches requires-python >=$req" \
  || bad "Dockerfile python:${img:-?} vs requires-python >=${req:-?} — builds clean, dies on import"

# One engine named keplaria. services.py finds-or-creates by display name, so a
# second engine means a stray duplicate and a non-deterministic session backend.
# There is no `gcloud ai reasoning-engines` surface — this is REST-only.
names=$(curl -s -m 30 -H "Authorization: Bearer $(gcloud auth print-access-token 2>/dev/null)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/584548214478/locations/us-central1/reasoningEngines" \
  2>/dev/null | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
print("\n".join(e.get("displayName","?") for e in d.get("reasoningEngines",[])))' 2>/dev/null)
rc=$?
count=$(printf '%s' "$names" | grep -c . || true)
if [ "$rc" -ne 0 ]; then
  meh "could not list reasoning engines (API/auth?)"
elif [ "$count" -eq 1 ] && [ "$names" = "keplaria" ]; then
  ok "exactly one reasoning engine, named keplaria"
elif [ "$count" -eq 0 ]; then
  bad "no reasoning engine deployed — the agent graph is down (judging needs it through Oct 1)"
else
  bad "expected 1 engine named keplaria, found $count: $(printf '%s' "$names" | tr '\n' ' ')— strays surface in Agent Registry"
fi

echo "== thin vertical =="
gcloud firestore databases list --format='value(name)' --project=keplaria 2>/dev/null \
  | grep -q '(default)$' \
  && ok "firestore (default) database" || bad "firestore (default) database missing"
gcloud pubsub topics describe keplaria-events --project=keplaria >/dev/null 2>&1 \
  && ok "keplaria-events topic" || bad "keplaria-events topic missing"
gcloud pubsub subscriptions describe keplaria-events-push --project=keplaria \
  --format='value(pushConfig.oidcToken.serviceAccountEmail)' 2>/dev/null \
  | grep -q 'keplaria-pubsub-push' \
  && ok "push subscription authenticates with OIDC" \
  || bad "keplaria-events-push missing its OIDC service account — the endpoint would accept anonymous pushes"
ingress_url=$(gcloud run services describe keplaria-ingress --region=us-central1 \
  --format='value(status.url)' --project=keplaria 2>/dev/null)
if [ -n "$ingress_url" ]; then
  # /healthz is NOT valid auth evidence here: it 404s both anonymously and
  # with a valid identity token, so it proves nothing about the IAM check.
  # POST /pubsub/push goes through the same Cloud Run IAM gate and is
  # rejected before the app code ever sees it, so it is a real witness.
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${ingress_url}/pubsub/push" \
    -H 'Content-Type: application/json' -d '{"message":{"data":""}}' 2>/dev/null)
  case "$code" in
    401|403) ok "ingress rejects anonymous POST /pubsub/push ($code)" ;;
    *)       bad "ingress returned $code to an anonymous POST /pubsub/push — expected 401/403" ;;
  esac
else
  bad "keplaria-ingress service not deployed"
fi
gcloud components list --filter='id=cloud-firestore-emulator' --format='value(state)' 2>/dev/null \
  | grep -q 'Installed' \
  && ok "firestore emulator component installed" \
  || meh "firestore emulator component not installed (gcloud components install cloud-firestore-emulator)"
# The Agent Runtime engine allows only 1 concurrent query; a serialised
# ingress is what stops a single 429 from becoming a redelivery storm (see
# infra/events/setup.sh for the incident this guards against).
concurrency_scale=$(gcloud run services describe keplaria-ingress --region=us-central1 \
  --project=keplaria \
  --format='value(spec.template.spec.containerConcurrency,spec.template.metadata.annotations["autoscaling.knative.dev/maxScale"])' 2>/dev/null)
if [ "$concurrency_scale" = "$(printf '1\t1')" ]; then
  ok "ingress containerConcurrency=1 and maxScale=1 (serialised against the engine's 1-concurrent-query quota)"
else
  bad "ingress concurrency/maxScale = '${concurrency_scale:-unknown}', expected 1 and 1 — a 429 can become a redelivery storm"
fi
retry_backoff=$(gcloud pubsub subscriptions describe keplaria-events-push --project=keplaria \
  --format='value(retryPolicy.minimumBackoff)' 2>/dev/null)
[ -n "$retry_backoff" ] \
  && ok "keplaria-events-push has a retry policy (minimumBackoff=$retry_backoff)" \
  || bad "keplaria-events-push has no retry policy — a 429/503 redelivers near-instantly and can exhaust the engine quota"

echo "== console + review services =="
console_url=$(gcloud run services describe keplaria-console --region=us-central1 \
  --format='value(status.url)' --project=keplaria 2>/dev/null)
if [ -n "$console_url" ]; then
  # Probes "/", not "/healthz". Observed 2026-08-16: a request to /healthz on a
  # *.run.app URL comes back 404 with a Google-styled error page, on BOTH this
  # service and keplaria-ingress, even though each registers the route and the
  # deployed console's own /openapi.json lists it. Every other path on the same
  # services reaches the container. Whatever eats it sits in front of Cloud Run,
  # so a /healthz probe can never pass from outside and says nothing about the
  # app. "/" is the better check anyway: it is the page a judge actually opens.
  code=$(curl -s -o /dev/null -w '%{http_code}' "$console_url/")
  [ "$code" = "200" ] && ok "public console answers unauthenticated (200)" \
    || bad "public console returned $code unauthenticated"
else
  meh "keplaria-console not deployed yet"
fi

review_url=$(gcloud run services describe keplaria-review --region=us-central1 \
  --format='value(status.url)' --project=keplaria 2>/dev/null)
if [ -n "$review_url" ]; then
  # The whole point of the service: an unauthenticated caller gets nothing.
  code=$(curl -s -o /dev/null -w '%{http_code}' "$review_url/review")
  case "$code" in
    401|403) ok "review service refuses unauthenticated callers ($code)" ;;
    302|303|307)
      # IAP fronts a browser surface, so it bounces an anonymous caller to
      # Google sign-in instead of refusing flatly. That is still "gets
      # nothing" — but only if the bounce goes to Google. A redirect
      # anywhere else would mean something other than IAP answered.
      loc=$(curl -sI "$review_url/review" | tr -d '\r' | sed -n 's/^[Ll]ocation: //p')
      case "$loc" in
        https://accounts.google.com/*)
          ok "review service bounces anonymous callers to Google sign-in ($code)" ;;
        *)
          bad "review service redirected ($code) somewhere other than Google sign-in: ${loc:-<none>}" ;;
      esac ;;
    *) bad "review service returned $code to an unauthenticated caller" ;;
  esac
  aud=$(gcloud run services describe keplaria-review --region=us-central1 \
    --project=keplaria --format='value(spec.template.spec.containers[0].env)' 2>/dev/null \
    | grep -c IAP_AUDIENCE)
  [ "$aud" -ge 1 ] && ok "review service has IAP_AUDIENCE set" \
    || bad "review service missing IAP_AUDIENCE — it will refuse every decision"
else
  meh "keplaria-review not deployed yet"
fi

echo "== MCP: adk-docs probe (known failure mode: mcp>=2 breaks mcpdoc with a misleading -32000) =="
probe='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"doctor","version":"0"}}}'
if command -v uvx >/dev/null; then
  resp=$(printf '%s\n' "$probe" | timeout 60 uvx --from mcpdoc --with 'mcp[cli]<2' \
    mcpdoc --urls 'AgentDevelopmentKit:https://adk.dev/llms.txt' --transport stdio 2>/dev/null | head -1)
  echo "$resp" | grep -q 'llms-txt' && ok "adk-docs MCP server handshakes" || meh "adk-docs MCP probe failed (check network / mcp pin)"
fi

echo "== dead-letter and sweep =="
DLQ_TOPIC_PATH=$(gcloud pubsub topics describe keplaria-events-dead \
  --project=keplaria --format='value(name)' 2>/dev/null || true)
[ -n "$DLQ_TOPIC_PATH" ] \
  && ok "dead-letter topic keplaria-events-dead exists" \
  || bad "dead-letter topic keplaria-events-dead missing — a stuck event is dropped at retention"

DLQ_ATTEMPTS=$(gcloud pubsub subscriptions describe keplaria-events-push \
  --project=keplaria --format='value(deadLetterPolicy.maxDeliveryAttempts)' 2>/dev/null || true)
[ "$DLQ_ATTEMPTS" = "5" ] \
  && ok "keplaria-events-push dead-letters after 5 deliveries" \
  || bad "keplaria-events-push has no dead-letter policy (maxDeliveryAttempts='${DLQ_ATTEMPTS:-unset}') — stuck events expire silently"

# Both bindings or dead-lettering silently does not happen. Checked separately
# from the policy above because the policy can be set and look correct while
# the agent lacks permission to act on it.
PROJECT_NUMBER=$(gcloud projects describe keplaria --format='value(projectNumber)' 2>/dev/null || true)
PUBSUB_AGENT="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud pubsub topics get-iam-policy keplaria-events-dead --project=keplaria \
  --format=json 2>/dev/null | grep -q "$PUBSUB_AGENT" \
  && ok "pubsub service agent can publish to the dead-letter topic" \
  || bad "pubsub service agent lacks publisher on keplaria-events-dead — dead-lettering will silently not happen"
gcloud pubsub subscriptions get-iam-policy keplaria-events-push --project=keplaria \
  --format=json 2>/dev/null | grep -q "$PUBSUB_AGENT" \
  && ok "pubsub service agent can ack on keplaria-events-push" \
  || bad "pubsub service agent lacks subscriber on keplaria-events-push — dead-lettering will silently not happen"

SWEEP_STATE=$(gcloud scheduler jobs describe keplaria-command-sweep \
  --location=us-central1 --project=keplaria --format='value(state)' 2>/dev/null || true)
[ "$SWEEP_STATE" = "ENABLED" ] \
  && ok "keplaria-command-sweep is ENABLED (failed commands are re-driven unattended)" \
  || bad "keplaria-command-sweep state='${SWEEP_STATE:-missing}' — a failed command gets no second chance"

# The emulator does not enforce collection-group indexes, so the sweep's unit
# tests pass locally whether or not this exists in production.
#
# Checked in BOTH databases, and named individually when one is missing. The
# index is needed in "(default)" for the deployed sweep and /review/failures,
# and in "keplaria-test" because that is the database `uv run pytest` uses
# whenever FIRESTORE_EMULATOR_HOST is unset (see tests/conftest.py) — and
# `gcloud firestore indexes composite list` without --database inspects only
# "(default)", so a check written that way reports green while the repo's own
# default test run fails on a missing index in the other database, with
# nothing anywhere surfacing the gap.
missing_index=""
for fsdb in "(default)" "keplaria-test"; do
  gcloud firestore indexes composite list --database="$fsdb" --project=keplaria \
    --format=json 2>/dev/null | grep -q '"collectionGroup": "outbox"' \
    || missing_index="${missing_index}${fsdb} "
done
[ -z "$missing_index" ] \
  && ok "outbox collection-group index present in (default) and keplaria-test (the sweep's query needs it)" \
  || meh "no outbox collection-group index in: ${missing_index}— the sweep query and /review/failures fail there; the emulator hides this because it auto-indexes"

echo
printf '%d passed, %d failed, %d warnings\n' "$pass" "$fail" "$warn"
[ "$fail" -eq 0 ]
