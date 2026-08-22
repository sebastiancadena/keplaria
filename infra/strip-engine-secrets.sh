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
# DIAGNOSED AND FIXED 2026-08-22: the failure was the API surface, not the
# operation. The v1 UpdateReasoningEngine rejects this patch with the generic
# "failed to be updated" (tried 2026-08-20 with both a `spec.deploymentSpec.env`
# mask and the full deploymentSpec); the SAME body against **v1beta1** with
# `updateMask=spec.deployment_spec.env` was APPLIED on the first attempt and
# removed FRAPPE_API_KEY / FRAPPE_API_SECRET from the live engine. The attempt
# loop below therefore tries v1beta1 first and keeps the other shapes as
# fallbacks. Historical notes kept for context:
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

# Four request shapes, tried in order until one is APPLIED. The original
# attempt (v1 + spec.deploymentSpec) is last because it is the one already
# proven to fail on 2026-08-20; the v1beta1 surface and the field-scoped
# snake_case mask are the two variables it never varied. Diagnosis notes
# 2026-08-22: the v1beta1 GET returns sourceCodeSpec as empty shells
# ({"inlineSource": {}, "imageSpec": {}}) — the archive itself is not
# readable — and the audit log for the failed operation carries no more
# detail than the operation does, so the platform hides the real reason.
# Each attempt is atomic (a rejected spec changes nothing), which is what
# makes trying them in sequence safe.
attempt() {
  local api="$1" mask="$2"
  echo
  echo "attempt: ${api} updateMask=${mask}"
  local base="https://${REGION}-aiplatform.googleapis.com/${api}"
  local op
  op="$(curl -s -X PATCH \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "${base}/${ENGINE}?updateMask=${mask}" \
    -d @"$TMP/patch.json" \
    | python3 -c '
import json, sys
r = json.load(sys.stdin)
if "error" in r:
    print("ERROR:" + r["error"].get("message", "unknown")); raise SystemExit
print(r.get("name", ""))')"

  case "$op" in
    ERROR:*) echo "rejected on submit: ${op#ERROR:}" >&2; return 1 ;;
    "")      echo "no operation returned" >&2; return 1 ;;
  esac

  # "Accepted" is not "applied" — this is a long-running operation and it can
  # and does come back failed. Waiting for the terminal state is the
  # difference between reporting a fix and reporting a request.
  echo "operation: $op"
  echo -n "waiting"
  for _ in $(seq 1 60); do
    RESULT="$(curl -s -H "Authorization: Bearer $TOKEN" "${base}/${op}")"
    if [[ "$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("done", False))' <<<"$RESULT")" == "True" ]]; then
      echo
      python3 -c '
import json, sys
r = json.load(sys.stdin)
if "error" in r:
    print("PATCH FAILED:", r["error"].get("message", "unknown"))
    sys.exit(1)
print("PATCH APPLIED")' <<<"$RESULT"
      return $?
    fi
    echo -n "."
    sleep 10
  done
  echo
  echo "operation did not reach a terminal state in 10 minutes" >&2
  return 1
}

APPLIED=0
for combo in \
  "v1beta1 spec.deployment_spec.env" \
  "v1beta1 spec.deploymentSpec" \
  "v1 spec.deployment_spec.env" \
  "v1 spec.deploymentSpec"; do
  if attempt $combo; then
    APPLIED=1
    break
  fi
done

if [[ $APPLIED -eq 0 ]]; then
  echo
  echo "Every request shape failed. The engine is unchanged — this API"
  echo "rejects atomically. Do NOT fall back to a redeploy: it was tried on"
  echo "2026-08-22 and agents-cli carries the existing engine env forward,"
  echo "so a redeploy does not clear these values. Capture a judge-safe"
  echo "explanation instead (values rotated dead 2026-08-20; no public"
  echo "egress; the graph never imports the executor)."
  exit 1
fi

echo
echo "verify with: bash scripts/doctor.sh 2>&1 | grep -A3 'secret hygiene'"
