#!/usr/bin/env bash
# Render one narration beat with the settled voice treatment.
#
#   video/narrate.sh <script.txt> <out.wav> [target_seconds] [semitones]
#
# ENGINE. Google Cloud Text-to-Speech, voice en-US-Chirp3-HD-Charon, no style
# prompt (user's call after a listening test on 2026-08-26, day 15). It
# replaced the stock Chatterbox speaker, which read as a sneering narrator at
# every setting tried across three viewings; a Gemini-TTS voice was heard in
# the same test and rejected on pace (it read ~1.6x slower than the timeline
# allows). Rides on ADC in the `keplaria` project; the API is enabled there.
# No voice cloning, no reference clip: the voice is a stock Google one, and
# THIRD_PARTY.md says so.
#
#   PITCH. Off by default; the fourth argument shifts by semitones through
#   rubberband with formant=preserved (asetrate + atempo drags the formants
#   down and yields a muffled voice rather than a lower one).
#
#   PACE, AND WHY IT IS A TARGET RATHER THAN A CONSTANT. The engine's length
#   for a given text is not something the script's word budget can predict, so
#   a fixed tempo cannot hit a time slot. Pass the beat's slot in seconds and
#   the tempo is computed from what actually came out.
#
# The stretch is clamped to 0.80-1.15. Outside that band rubberband starts to
# sound like rubberband, and a beat that needs more than a 20% stretch needs
# its copy cut instead -- the script says so out loud rather than quietly
# mangling the take.
set -euo pipefail

SCRIPT=${1:?usage: narrate.sh <script.txt> <out.wav> [target_seconds] [semitones]}
OUT=${2:?usage: narrate.sh <script.txt> <out.wav> [target_seconds] [semitones]}
TARGET=${3:-0}
SCRIPT=$(realpath "$SCRIPT"); OUT=$(realpath -m "$OUT")
SEMITONES=${4:-0}

VOICE=${KEPLARIA_TTS_VOICE:-en-US-Chirp3-HD-Charon}
PROJECT=${GOOGLE_CLOUD_PROJECT:-keplaria}
RAW=$(mktemp --suffix=.wav)
trap 'rm -f "$RAW"' EXIT

python3 - "$SCRIPT" "$RAW" "$VOICE" "$PROJECT" <<'PY'
import base64, json, subprocess, sys, urllib.request
script, raw, voice, project = sys.argv[1:5]
tok = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
body = {"input": {"text": open(script).read().strip()},
        "voice": {"languageCode": "en-US", "name": voice},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000}}
req = urllib.request.Request(
    "https://texttospeech.googleapis.com/v1beta1/text:synthesize",
    data=json.dumps(body).encode(),
    headers={"Authorization": f"Bearer {tok}", "x-goog-user-project": project,
             "Content-Type": "application/json"})
try:
    reply = json.load(urllib.request.urlopen(req))
except urllib.error.HTTPError as e:
    sys.exit(f"FAIL: text-to-speech {e.code}: {e.read().decode()[:400]}")
open(raw, "wb").write(base64.b64decode(reply["audioContent"]))
PY

read -r RATIO TEMPO < <(python3 - "$RAW" "$TARGET" "$SEMITONES" <<'PY'
import sys, wave
raw, target, semitones = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
with wave.open(raw) as w:
    secs = w.getnframes() / w.getframerate()
tempo = 1.0
if target > 0:
    tempo = secs / target           # <1 stretches the take out, >1 tightens it
    clamped = min(max(tempo, 0.80), 1.15)
    if abs(clamped - tempo) > 1e-9:
        print(f"WARN: {secs:.1f}s into a {target:.1f}s slot needs tempo "
              f"{tempo:.2f}; clamped to {clamped:.2f}. Cut the copy instead.",
              file=sys.stderr)
    tempo = clamped
print(f"{2 ** (semitones / 12):.6f} {tempo:.6f}")
PY
)

ffmpeg -y -v error -i "$RAW" \
  -af "rubberband=pitch=${RATIO}:tempo=${TEMPO}:formant=preserved" "$OUT"

python3 - "$OUT" "$SCRIPT" "$TARGET" <<'PY'
import sys, wave
out, script, target = sys.argv[1], sys.argv[2], float(sys.argv[3])
with wave.open(out) as w:
    secs = w.getnframes() / w.getframerate()
words = len(open(script).read().split())
slot = f" (slot {target:.1f}s)" if target else ""
print(f"{out}: {secs:.1f}s{slot}, {words} words, {words / secs * 60:.0f} wpm")
PY
