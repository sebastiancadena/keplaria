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

echo "== project =="
[ -d .venv ] && uv lock --check >/dev/null 2>&1 \
  && ok "uv.lock consistent with pyproject" || meh ".venv/lock drift (run: uv sync)"

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
