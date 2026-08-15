#!/usr/bin/env bash
# Domain eval harness: seed → generate → grade → save evidence.
#
# Makes LIVE Gemini calls (global endpoint) for the coordinator and evidence
# agents; everything else is local (Firestore emulator, yente stub, fixture
# documents). No ERP calls happen: the graph only queues Firestore commands
# and the executor never runs here.
#
# Prereq: the Firestore emulator must be listening on 8451 — it dies
# silently, so this script checks first:
#     gcloud emulators firestore start --host-port=localhost:8451
set -euo pipefail
cd "$(dirname "$0")/../.."

if ! nc -z localhost 8451 2>/dev/null; then
  echo "Firestore emulator not listening on 8451. Start it with:" >&2
  echo "  gcloud emulators firestore start --host-port=localhost:8451" >&2
  exit 1
fi

set -a; source .env; set +a
export FIRESTORE_EMULATOR_HOST=localhost:8451
export FIRESTORE_PROJECT_ID=keplaria
export GOOGLE_CLOUD_PROJECT=keplaria
export FIRESTORE_DATABASE='(default)'
# gemini-3.6-flash is only served from the global endpoint (README
# "Operational constraints"); .env carries us-central1 for the engine.
export GOOGLE_CLOUD_LOCATION=global
export YENTE_BASE_URL=http://127.0.0.1:8452

uv run python tests/eval/yente_stub.py &
STUB_PID=$!
trap 'kill "$STUB_PID" 2>/dev/null || true' EXIT

uv run python tests/eval/seed.py

# NOT `agents-cli eval generate`: its SSE parser rejects the state-only
# events Workflow function nodes emit (see generate_traces.py docstring).
# Grading stays official.
rm -f artifacts/traces/domain_traces.json
uv run python tests/eval/generate_traces.py
agents-cli eval grade --traces artifacts/traces/domain_traces.json --metrics domain_case_pass

latest=$(ls -t artifacts/grade_results/results_*.json | head -1)
mkdir -p spikes/domain_evals/history
cp "$latest" spikes/domain_evals/evidence.json
# Score history is part of the gate evidence: keep every graded run, not
# just the flattering latest one.
cp "$latest" "spikes/domain_evals/history/$(basename "$latest")"
echo "evidence saved: spikes/domain_evals/evidence.json (from $latest)"
