#!/usr/bin/env python3
"""Render one narration beat as a sequence of UTTERANCES, not one TTS call.

    video/narrate_beat.py <beat.txt> <out.wav> [--start S --timing timing.json
                          --sync beat.sync] [--fit SECONDS] [--rate R]
                          [--utt-dir DIR] [--plan out.json]

WHY UTTERANCES. The engine (Cloud TTS, Chirp 3 HD Charon; see narrate.sh for
the voice decision) decides prosody from punctuation and sentence shape, and a
whole beat in one request gave it nothing to anchor on: SYNC pauses did not
exist, a clause split across two beat files opened cold ("And a schema-valid
worker count...", "Keplaria dot com."), and a stretched take flattened what
intonation there was. The user heard all three on 2026-08-27. Here every
non-empty line of the beat file is a sentence that stands on its own, is
synthesised in its own request, and is placed on the beat's timeline:

  * consecutive lines are separated by GAP seconds; a blank line between two
    utterances asks for the long gap instead (a beat, in the radio sense);
  * a line named in the .sync file ("key: phrase" or "key-1.5: phrase") does
    not start before takes/timing.json[key] (+ offset) relative to --start;
    if the event has already passed, it starts at its natural time and the
    lag is reported, never hidden;
  * the whole beat can be fitted to --fit seconds by the engine's own
    speakingRate (one re-synthesis, clamped 0.90-1.12; beyond that the copy
    is what needs to change, and the script says so).

PAUSE MARKUP IS A TRAP. Chirp reads "[pause long]" aloud as the words "pause
long" when it arrives in `input.text` (probed 2026-08-27); only `input.markup`
honours it. Pauses here are silence laid down by ffmpeg, never markup, so a
beat cannot be one field name away from narrating its own stage directions.

The per-utterance wavs are kept (--utt-dir) so listen.py can judge each one
alone, and the plan (--plan) records where every utterance landed and how the
beat was placed, so that reroll() can re-synthesise ONE utterance the listener
rejected and re-place the beat: the engine's output varies between requests
(a sentence that read as a statement once can read as a question the next
time), so a failed take is re-rolled before the copy is blamed.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

GAP = 0.30
LONG_GAP = 0.85
RATE_MIN, RATE_MAX = 0.90, 1.12
LAG_WARN = 4.0
LENGTH_TAKES = 2
VOICE = "en-US-Chirp3-HD-Charon"
PROJECT = "keplaria"
SR = 24000


def synth(text: str, rate: float, out: Path, token: str) -> float:
    cfg = {"audioEncoding": "LINEAR16", "sampleRateHertz": SR}
    if abs(rate - 1.0) > 1e-6:
        cfg["speakingRate"] = round(rate, 3)
    body = {"input": {"text": text},
            "voice": {"languageCode": "en-US", "name": VOICE},
            "audioConfig": cfg}
    req = urllib.request.Request(
        "https://texttospeech.googleapis.com/v1beta1/text:synthesize",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "x-goog-user-project": PROJECT,
                 "Content-Type": "application/json"})
    try:
        reply = json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        sys.exit(f"FAIL: text-to-speech {e.code}: {e.read().decode()[:400]}")
    out.write_bytes(base64.b64decode(reply["audioContent"]))
    return duration(out)


def duration(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def parse_beat(path: Path) -> list[dict]:
    """Lines → utterances; a blank line marks a long gap before the next one."""
    utts, long_before = [], False
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            long_before = bool(utts)
            continue
        utts.append({"text": line, "long_gap": long_before})
        long_before = False
    if not utts:
        sys.exit(f"FAIL: {path} has no utterances")
    return utts


def parse_sync(path: Path | None) -> list[tuple[str, float, str]]:
    if path is None or not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, phrase = line.partition(":")
        key, offset = key.strip(), 0.0
        for sign in ("+", "-"):
            if sign in key:
                key, _, off = key.partition(sign)
                offset = float(sign + off)
        out.append((key.strip(), offset, phrase.strip()))
    return out


def place(utts, durs, sync, timing, start):
    """Sequential placement with sync waits. Returns (offsets, lags, total)."""
    anchors = {}
    for key, off, phrase in sync:
        hits = [i for i, u in enumerate(utts) if phrase in u["text"]]
        if len(hits) != 1:
            sys.exit(f"FAIL: sync phrase {phrase!r} matches {len(hits)} utterances")
        if key not in timing and key + "s" in timing:   # "erp_row" vs timing's "erp_rows"
            key = key + "s"
        anchors[hits[0]] = (key, timing[key] + off - start)
    t, offsets, lags = 0.0, [], {}
    for i, (u, d) in enumerate(zip(utts, durs)):
        if i:
            t += LONG_GAP if u["long_gap"] else GAP
        if i in anchors:
            key, want = anchors[i]
            lags[key] = round(t - want, 2)      # >0: narration lands after the event
            t = max(t, want)
        offsets.append(round(t, 3))
        t += d
    return offsets, lags, t


def concat(utt_paths, offsets, total, out: Path):
    inputs, chains, tags = [], [], []
    for k, (p, off) in enumerate(zip(utt_paths, offsets)):
        inputs += ["-i", str(p)]
        ms = int(round(off * 1000))
        chains.append(f"[{k}:a]adelay={ms}|{ms}[u{k}]")
        tags.append(f"[u{k}]")
    chains.append("".join(tags) + f"amix=inputs={len(tags)}:normalize=0:duration=longest,"
                  f"apad=whole_dur={total:.3f},atrim=0:{total:.3f}[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chains),
                    "-map", "[a]", "-ar", str(SR), "-ac", "1", str(out)], check=True)


def build_plan(beat, out, utts, paths, offsets, durs, rate, total, lags, a):
    words = sum(len(u["text"].split()) for u in utts)
    return {"beat": beat.stem, "rate": round(rate, 3), "seconds": round(total, 3),
            "fit": a.fit or None, "words": words, "lags": lags,
            "placement": {"beat_file": str(beat), "out": str(out), "start": a.start,
                          "timing": a.timing, "sync": a.sync},
            "utterances": [{"text": u["text"], "wav": str(p), "start": o, "seconds": round(d, 3),
                            "long_gap": u["long_gap"]}
                           for u, p, o, d in zip(utts, paths, offsets, durs)]}


def replace(plan_path: Path, start: float | None = None, durs: list[float] | None = None) -> dict:
    """Re-place a rendered beat from its utterance wavs (at a new start, or after a take was
    swapped), re-concat it and rewrite the plan. No synthesis."""
    plan = json.loads(plan_path.read_text())
    pl = plan["placement"]
    if start is not None:
        pl["start"] = start
    utts = [{"text": u["text"], "long_gap": u.get("long_gap", False)} for u in plan["utterances"]]
    paths = [Path(u["wav"]) for u in plan["utterances"]]
    durs = durs or [duration(p) for p in paths]
    timing = json.loads(Path(pl["timing"]).read_text()) if pl.get("timing") else {}
    sync = parse_sync(Path(pl["sync"])) if pl.get("sync") else []
    offsets, lags, total = place(utts, durs, sync, timing, pl.get("start") or 0.0)
    out = Path(pl["out"])
    concat(paths, offsets, total, out)

    class A:  # the argparse namespace build_plan reads
        fit, start, timing, sync = plan.get("fit") or 0.0, pl.get("start"), pl.get("timing"), pl.get("sync")
    new = build_plan(Path(pl["beat_file"]), out, utts, paths, offsets, durs, plan["rate"], total, lags, A)
    plan_path.write_text(json.dumps(new, indent=1) + "\n")
    return new


def reroll(plan_path: Path, index: int, token: str | None = None) -> dict:
    """Re-synthesise utterance `index` of a rendered beat (same rate), then re-place the beat."""
    plan = json.loads(plan_path.read_text())
    token = token or subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    u = plan["utterances"][index]
    synth(u["text"], plan["rate"], Path(u["wav"]), token)
    return replace(plan_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("beat"); ap.add_argument("out")
    ap.add_argument("--start", type=float, default=None, help="beat start, in the take's clock")
    ap.add_argument("--timing", default=None); ap.add_argument("--sync", default=None)
    ap.add_argument("--fit", type=float, default=0.0, help="fit the beat into this many seconds")
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--utt-dir", default=None); ap.add_argument("--plan", default=None)
    a = ap.parse_args()

    beat, out = Path(a.beat), Path(a.out)
    utts = parse_beat(beat)
    timing = json.loads(Path(a.timing).read_text()) if a.timing else {}
    sync = parse_sync(Path(a.sync)) if a.sync else []
    if sync and a.start is None:
        sys.exit("FAIL: --sync needs --start")
    utt_dir = Path(a.utt_dir) if a.utt_dir else out.with_suffix("") / "utt"
    utt_dir.mkdir(parents=True, exist_ok=True)
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()

    rate = a.rate
    for attempt in (1, 2):
        paths = [utt_dir / f"{i + 1:02d}.wav" for i in range(len(utts))]
        durs = [synth(u["text"], rate, p, token) for u, p in zip(utts, paths)]
        offsets, lags, total = place(utts, durs, sync, timing, a.start or 0.0)
        if not a.fit or total <= a.fit + 0.05 or attempt == 2:
            break
        # speech time only: gaps and sync waits do not shrink with rate
        speech = sum(durs)
        slack = total - a.fit
        want = rate * speech / max(speech - slack, 0.1)
        rate = min(max(want, RATE_MIN), RATE_MAX)
    # the engine's length for the same text varies ~15% between requests: when the beat still
    # overshoots after the clamp, take each utterance again and keep the shorter take (listen.py
    # judges the prosody of whatever survives)
    for _ in range(LENGTH_TAKES):
        if not a.fit or total <= a.fit + 0.3:
            break
        for i, u in enumerate(utts):
            alt = utt_dir / f"{i + 1:02d}.alt.wav"
            d = synth(u["text"], rate, alt, token)
            if d < durs[i] - 0.05:
                alt.replace(paths[i]); durs[i] = d
            else:
                alt.unlink()
        offsets, lags, total = place(utts, durs, sync, timing, a.start or 0.0)
    if a.fit and total > a.fit + 0.3:
        print(f"WARN: {beat.name}: {total:.1f}s into a {a.fit:.1f}s slot at rate {rate:.2f} "
              f"(clamp {RATE_MAX}) after {LENGTH_TAKES} extra takes. Cut the copy instead.", file=sys.stderr)
    concat(paths, offsets, total, out)

    plan = build_plan(beat, out, utts, paths, offsets, durs, rate, total, lags, a)
    if a.plan:
        Path(a.plan).write_text(json.dumps(plan, indent=1) + "\n")
    for key, lag in lags.items():
        flag = "  <-- late" if lag > LAG_WARN else ""
        print(f"  sync {key}: {lag:+.1f}s{flag}")
    words = plan["words"]
    print(f"{out}: {total:.1f}s{f' (fit {a.fit:.1f}s)' if a.fit else ''}, {len(utts)} utterances, "
          f"{words} words, rate {rate:.2f}, {words / max(sum(durs), 0.1) * 60:.0f} wpm spoken")


if __name__ == "__main__":
    main()
