#!/usr/bin/env bash
# Render one narration beat with the settled voice treatment.
#
#   video/narrate.sh <script.txt> <out.wav> [target_seconds] [semitones]
#
# No voice cloning: the stock Chatterbox speaker is used deliberately (user's
# call, 2026-08-22). Two corrections sit on top of it.
#
#   PITCH. Off by default since 2026-08-26: the -2 semitone shift, stacked on
#   the low exaggeration/cfg, read as a sneering, unnatural narrator on the
#   first full viewing (user's call after a four-way listening test). The
#   shift remains available as the fourth argument; if used, it goes through
#   rubberband with formant=preserved, because asetrate + atempo drags the
#   formants down and yields a muffled voice rather than a lower one.
#
#   PACE, AND WHY IT IS A TARGET RATHER THAN A CONSTANT. Chatterbox is
#   stochastic: the SAME text renders a different length on every run -- 11.1s
#   and 11.8s were measured back to back here. So a fixed tempo cannot hit a
#   time slot, and the script's word budget cannot be trusted to produce a
#   duration. Pass the beat's slot in seconds and the tempo is computed from
#   what actually came out.
#
# The stretch is clamped to 0.80-1.15. Outside that band rubberband starts to
# sound like rubberband, and a beat that needs more than a 20% stretch needs
# its copy cut instead -- the script says so out loud rather than quietly
# mangling the take.
set -euo pipefail

SCRIPT=${1:?usage: narrate.sh <script.txt> <out.wav> [target_seconds] [semitones]}
OUT=${2:?usage: narrate.sh <script.txt> <out.wav> [target_seconds] [semitones]}
TARGET=${3:-0}
# Absolute paths: the TTS call below cd's into the tool repo, so a relative
# script or output path silently pointed nowhere (found 2026-08-24).
SCRIPT=$(realpath "$SCRIPT"); OUT=$(realpath -m "$OUT")
SEMITONES=${4:-0}

VIDEO_REPO=${KEPLARIA_VIDEO_REPO:-$HOME/dev/git/byteql-video}
RAW=$(mktemp --suffix=.wav)
trap 'rm -f "$RAW"' EXIT

# Calm delivery: the 0.5/0.5 defaults read as an advert. Lower exaggeration
# and cfg give a narrator instead of a pitchman.
( cd "$VIDEO_REPO" && uv run scripts/narrate.py "$SCRIPT" -o "$RAW" \
    --exaggeration 0.35 --cfg 0.35 >/dev/null 2>&1 )

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
