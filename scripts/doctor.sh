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

echo "== judge-visibility (private planning layer must not leak) =="
# The leak vector is our own writing: specs, plans and subagent briefs drafted
# from strategy/ carry risk IDs, plan-step labels and private filenames into
# code and commit messages when copied verbatim. Caught in app/risk.py and in a
# commit message on 2026-08-14. This grep is the automated backstop.
LEAK_RE='flight plan|architecture-contracts|risk-register|gates-and-cut|scoring-constitution|execution-plan|demo-and-video|Ground Control|\bR[1-9][0-9]?\b'
leak_files=$(git grep -lEi "$LEAK_RE" -- ':!strategy' ':!scripts/doctor.sh' 2>/dev/null)
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
[ -f policy/supplier_risk.v1.json ] \
  && python3 -c "import json; json.load(open('policy/supplier_risk.v1.json'))" >/dev/null 2>&1 \
  && ok "policy fixture exists and parses (policy/supplier_risk.v1.json)" \
  || bad "policy fixture missing or does not parse — every case would fail closed to POLICY_UNAVAILABLE/blocked"
grep -qE '^policy/?$' .gcloudignore \
  && bad ".gcloudignore excludes policy/ — the runtime-required fixture would not ship, every case fails closed" \
  || ok "policy/ is not excluded from the deploy package"
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

echo "== MCP: adk-docs probe (known failure mode: mcp>=2 breaks mcpdoc with a misleading -32000) =="
probe='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"doctor","version":"0"}}}'
if command -v uvx >/dev/null; then
  resp=$(printf '%s\n' "$probe" | timeout 60 uvx --from mcpdoc --with 'mcp[cli]<2' \
    mcpdoc --urls 'AgentDevelopmentKit:https://adk.dev/llms.txt' --transport stdio 2>/dev/null | head -1)
  echo "$resp" | grep -q 'llms-txt' && ok "adk-docs MCP server handshakes" || meh "adk-docs MCP probe failed (check network / mcp pin)"
fi

echo
printf '%d passed, %d failed, %d warnings\n' "$pass" "$fail" "$warn"
[ "$fail" -eq 0 ]
