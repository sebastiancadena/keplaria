#!/usr/bin/env bash
# Remove secret-shaped plaintext env vars from the deployed reasoning engine.
#
# Why this exists: `agents-cli deploy` injects every key in .env as a plaintext
# runtime env var. Keeping secrets out of .env stops it happening again, but it
# does not clean an engine that already carries them — this does, without
# waiting for the next deploy.
#
# The engine has no public internet egress and its graph never imports the
# executor, so a Frappe credential on it is unused as well as readable. Nothing
# here changes behaviour; it only removes values the engine cannot use.
#
#   bash infra/strip-engine-secrets.sh [--apply]
#
# Without --apply it prints what it would remove and changes nothing.

set -euo pipefail

PROJECT="${PROJECT:-keplaria}"
REGION="${REGION:-us-central1}"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

TOKEN="$(gcloud auth print-access-token)"
BASE="https://${REGION}-aiplatform.googleapis.com/v1"

# Discover the engine rather than hardcoding its id — a redeploy under a new
# name would otherwise leave this script silently cleaning nothing.
ENGINE="$(curl -s -H "Authorization: Bearer $TOKEN" \
  "${BASE}/projects/${PROJECT}/locations/${REGION}/reasoningEngines" \
  | python3 -c '
import json, sys
engines = json.load(sys.stdin).get("reasoningEngines", [])
print(engines[0]["name"] if engines else "")')"

if [[ -z "$ENGINE" ]]; then
  echo "no reasoning engine found in ${PROJECT}/${REGION}" >&2
  exit 1
fi
echo "engine: $ENGINE"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -s -H "Authorization: Bearer $TOKEN" "${BASE}/${ENGINE}" > "$TMP/engine.json"

# Values are never printed — only key names — so running this cannot itself
# leak the credential it is removing.
python3 - "$TMP/engine.json" "$TMP/patch.json" <<'PY'
import json, sys

MARKERS = ("SECRET", "API_KEY", "TOKEN", "PASSWORD", "CREDENTIAL")
src, dst = sys.argv[1], sys.argv[2]
spec = json.load(open(src))["spec"]["deploymentSpec"]
env = spec.get("env", []) or []

drop = [e["name"] for e in env if any(m in e["name"] for m in MARKERS)]
keep = [e for e in env if e["name"] not in drop]

print("would remove:", ", ".join(drop) if drop else "(nothing)")
print("would keep  :", ", ".join(e["name"] for e in keep))
json.dump({"spec": {"deploymentSpec": {"env": keep}}}, open(dst, "w"))
sys.exit(0 if drop else 3)
PY
status=$?

if [[ $status -eq 3 ]]; then
  echo "nothing to do — no secret-shaped env vars on the engine"
  exit 0
fi

if [[ $APPLY -eq 0 ]]; then
  echo
  echo "dry run. re-run with --apply to patch the engine."
  exit 0
fi

echo "patching..."
curl -s -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  "${BASE}/${ENGINE}?updateMask=spec.deploymentSpec.env" \
  -d @"$TMP/patch.json" \
  | python3 -c '
import json, sys
r = json.load(sys.stdin)
if "error" in r:
    print("FAILED:", r["error"].get("message")); sys.exit(1)
print("patch accepted:", r.get("name", "(operation)"))'

echo
echo "verify with: bash scripts/doctor.sh 2>&1 | grep -A3 'secret hygiene'"
