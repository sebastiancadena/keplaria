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
#
# The patch sends the WHOLE deploymentSpec with env filtered, not just the env
# field, so the PSC interface config, resource limits and instance counts stay
# explicit rather than relying on the mask to leave them alone.
#
# KNOWN TO FAIL HERE, CAUSE UNDIAGNOSED (2026-08-20). Two attempts both came
# back "The Reasoning Engine failed to be updated" — first with a
# `spec.deploymentSpec.env` mask and an env-only body, then with the full
# deploymentSpec above. Neither is a case of asking for something unsupported:
# the v1 discovery document describes `env` as "Environment variables to be set
# with the Reasoning Engine deployment. The environment variables can be
# updated through the UpdateReasoningEngine API."
#
# What is ruled out: it is not a rebuild failure (Cloud Build logged nothing on
# either attempt), and it does no damage (the engine spec is byte-identical
# afterwards — this API rejects atomically). What is not ruled out: something
# about updating an engine deployed via `sourceCodeSpec`, which is what this one
# uses, and whose source the GET response does not return.
#
# THE FALLBACK BELOW WAS WRONG, AND WAS MEASURED WRONG ON 2026-08-22. This
# block used to say that a normal `agents-cli deploy` clears stale env, because
# .env no longer carries secrets. It does not. A deploy ran that day with a
# clean .env and the engine still carried FRAPPE_API_KEY and FRAPPE_API_SECRET
# afterwards: `agents-cli` carries the EXISTING engine's env forward on update
# rather than replacing the set with what .env holds. Proof by elimination —
# DEVTO_API_KEY lives only in .env.secrets and is absent from the engine, so
# that file was never read, and .env no longer holds the Frappe keys. There is
# also no removal flag: `agents-cli deploy --update-env-vars` only sets.
#
# So this script is not a convenience any more — it is the only known path, and
# it does not work yet. That makes the undiagnosed PATCH failure above the whole
# problem rather than a curiosity. Keep this script and diagnose it; do not
# reach for a redeploy, which has now been tried and recorded.
#
# Not urgent, and worth saying so next to the alarm: the values are dead (the
# Frappe secret was rotated 2026-08-20), the engine has no public egress, and
# its graph never imports the executor. This is hygiene on a readable field,
# not a live credential exposure, and it needs no further rotation first.
python3 - "$TMP/engine.json" "$TMP/patch.json" <<'PY'
import json, sys

MARKERS = ("SECRET", "API_KEY", "TOKEN", "PASSWORD", "CREDENTIAL")
src, dst = sys.argv[1], sys.argv[2]
spec = dict(json.load(open(src))["spec"]["deploymentSpec"])
env = spec.get("env", []) or []

drop = [e["name"] for e in env if any(m in e["name"] for m in MARKERS)]
keep = [e for e in env if e["name"] not in drop]
spec["env"] = keep

print("would remove:", ", ".join(drop) if drop else "(nothing)")
print("would keep  :", ", ".join(e["name"] for e in keep))
print("preserving  :", ", ".join(k for k in spec if k != "env"))
json.dump({"spec": {"deploymentSpec": spec}}, open(dst, "w"))
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
OP="$(curl -s -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  "${BASE}/${ENGINE}?updateMask=spec.deploymentSpec" \
  -d @"$TMP/patch.json" \
  | python3 -c '
import json, sys
r = json.load(sys.stdin)
if "error" in r:
    print("ERROR:" + r["error"].get("message", "unknown")); raise SystemExit
print(r.get("name", ""))')"

case "$OP" in
  ERROR:*) echo "${OP#ERROR:}" >&2; exit 1 ;;
  "")      echo "no operation returned" >&2; exit 1 ;;
esac

# "Accepted" is not "applied" — this is a long-running operation and it can and
# does come back failed. Waiting for the terminal state is the difference
# between reporting a fix and reporting a request.
echo "operation: $OP"
echo -n "waiting"
for _ in $(seq 1 60); do
  RESULT="$(curl -s -H "Authorization: Bearer $TOKEN" "${BASE}/${OP}")"
  if [[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("done", False))' <<<"$RESULT")" == "True" ]]; then
    echo
    python3 -c '
import json, sys
r = json.load(sys.stdin)
if "error" in r:
    print("PATCH FAILED:", r["error"].get("message", "unknown"))
    print()
    print("The engine is unchanged — this API rejects a bad spec atomically.")
    print("Fall back to a full redeploy, which now produces a clean engine")
    print("because the secrets no longer live in .env:")
    print("  agents-cli deploy --project keplaria --region us-central1 \\")
    print("    --network-attachment projects/keplaria/regions/us-central1/networkAttachments/keplaria-psc2")
    sys.exit(1)
print("PATCH APPLIED")' <<<"$RESULT"
    break
  fi
  echo -n "."
  sleep 10
done

echo
echo "verify with: bash scripts/doctor.sh 2>&1 | grep -A3 'secret hygiene'"
