#!/usr/bin/env python3
"""Three listeners for a rendered beat, so a wrong reading fails the build
before a human hears it.

    uv run --with librosa --with soundfile --env-file .env python video/listen.py <plan.json>...
        [--meaning-dir DIR] [--report out.json] [--reroll N]

plan.json is what narrate_beat.py --plan wrote: the utterances, their texts and
their wavs. For each utterance:

  1. ROUND-TRIP. A transcription of the audio alone is compared to the text
     word by word (numbers, punctuation and known name spellings normalised).
     Catches dropped words and mispronunciations.
  2. CONTOUR. The pitch track's last voiced third against the preceding third:
     a declarative sentence that ends more than ~2.5 semitones up was read as
     a question or a list item, which is the fault the user heard on
     2026-08-27 ("...stays, for what happens months later?"). No model, no
     opinion; runs in seconds.
  3. BLIND MEANING. A multimodal model hears the audio and NOTHING else, and
     says what the speaker meant and how it was delivered. A second, text-only
     call compares that paraphrase with the intended meaning written beside
     the script (<beat>.meaning, one line per utterance). The listener never
     sees the script text, so it cannot read the intent into the audio.

A failed utterance is first HEARD AGAIN (the transcriber drops a clause now
and then), and with --reroll N it is then RE-SYNTHESISED up to N times through
narrate_beat.reroll(): the engine's output varies between requests, and a
sentence that reads as a question in one take reads as a statement in the
next. Only an utterance that fails every take is a copy problem.

Exit status is non-zero when any utterance still fails. The report names the
utterance and the reason, so the fix is a rewrite of one line and a re-render
of one beat, not a listening session.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import narrate_beat as nb  # noqa: E402

MODEL = "gemini-3.6-flash"
ROUNDTRIP_MIN = 0.80
RISE_SEMITONES = 2.5

ALIASES = {  # how transcribers spell what the voice says
    "kepleria": "keplaria", "keplerria": "keplaria", "caplarea": "keplaria", "kepleria.com": "keplaria dot com",
    "keplaria.com": "keplaria dot com", "erpnext": "erp next", "erp-next": "erp next",
    "opentelemetry": "open telemetry", "re-drives": "redrives", "re-drive": "redrive",
    "near-match": "near match", "page-text": "page text", "station-keeping": "station keeping",
    "human-approval": "human approval", "schema-valid": "schema valid", "e.r.p.": "erp", "e.r.p": "erp",
}
NUMBERS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "12": "twelve",
           "380": "three hundred eighty", "80": "eighty"}


NAME = re.compile(r"\b[ck][ae]p[ae]?l+[aeo]?r+[iy]*[aeo]+s?(\.com)?\b")   # every spelling a transcriber gives the name


def norm(s: str) -> list[str]:
    s = s.lower().replace("’", "'")
    s = NAME.sub(lambda m: "keplaria" + (" dot com" if m.group(1) else ""), s)
    s = s.replace("stationkeeping", "station keeping")
    for k, v in ALIASES.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    toks = []
    for t in s.split():
        toks.extend(NUMBERS.get(t, t).split())
    return toks


def roundtrip(text: str, heard: str) -> tuple[float, list[str]]:
    a, b = norm(text), norm(heard)
    sm = difflib.SequenceMatcher(a=a, b=b)
    missing = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            missing += a[i1:i2]
    return sm.ratio(), missing


def contour(wav: Path) -> dict:
    import librosa
    y, sr = librosa.load(str(wav), sr=None)
    f0, voiced, _ = librosa.pyin(y, fmin=60, fmax=320, sr=sr, frame_length=2048)
    f0 = f0[voiced & ~np.isnan(f0)]
    if len(f0) < 12:
        return {"rise_semitones": 0.0, "voiced_frames": int(len(f0))}
    n = max(len(f0) // 3, 4)
    tail, body = np.median(f0[-n:]), np.median(f0[-2 * n:-n])
    return {"rise_semitones": round(float(12 * np.log2(tail / body)), 2),
            "voiced_frames": int(len(f0))}


def client():
    from google import genai
    return genai.Client(vertexai=True, project="keplaria", location="global")


def hear(c, wav: Path) -> dict:
    from google.genai import types
    prompt = (
        "You are listening to one sentence of narration. Reply with JSON only, keys: "
        "transcript (the exact words), meaning (one plain sentence: what the speaker is telling "
        "the listener), delivery (one of: statement, question, list_item, incomplete, other), "
        "stress (the one or two words that carry the emphasis), odd (true if any word or phrase "
        "is delivered in a way that would confuse a listener, else false), odd_why (short, or empty).")
    r = c.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=wav.read_bytes(), mime_type="audio/wav"), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0))
    return json.loads(r.text)


def judge(c, text: str, intended: str, heard: dict) -> dict:
    """Text-only. It may see the script (only the LISTENER must be blind); it decides whether a
    viewer who heard what the listener heard would come away with the intended meaning."""
    from google.genai import types
    prompt = (
        "Context: the narration describes a software system that onboards suppliers into an ERP. "
        "'Agent' means an AI agent, 'case' or 'Payload' a supplier case, 'hold' a purchasing hold, "
        "'executor' and 'coordinator' software components, 'Ground Control' a dashboard. "
        f"A narrator read this sentence from a script: {text!r}. The author's intended meaning: "
        f"{intended!r}. A listener who heard ONLY the audio reported: {json.dumps(heard)}. "
        "Would a viewer who understood it the way the listener did come away with the intended "
        "meaning? Omitted detail is fine; FAIL only when the listener understood something "
        "different, or misheard a word in a way that changes the sense, or reported the delivery "
        "as a question or cut off. Reply with JSON only: verdict (PASS or FAIL), reason (one short sentence).")
    r = c.models.generate_content(
        model=MODEL, contents=[prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0))
    return json.loads(r.text)


def listen_once(c, text: str, wav: Path, meaning: str | None) -> tuple[dict, list[str]]:
    row = {"fail": []}
    heard = hear(c, wav)
    ratio, missing = roundtrip(text, heard.get("transcript", ""))
    row["heard"], row["roundtrip"] = heard, round(ratio, 3)
    if ratio < ROUNDTRIP_MIN:
        row["fail"].append(f"round-trip {ratio:.2f}: heard {heard.get('transcript')!r}, lost {missing}")
    pc = contour(wav)
    row["contour"] = pc
    declarative = text.rstrip().endswith((".", "!"))
    if declarative and pc["rise_semitones"] > RISE_SEMITONES:
        row["fail"].append(f"pitch rises {pc['rise_semitones']:+.1f} st at the end of a statement")
    if heard.get("delivery") in ("question", "incomplete") and declarative:
        row["fail"].append(f"heard as {heard.get('delivery')}")
    if heard.get("odd"):
        row["fail"].append(f"odd delivery: {heard.get('odd_why')}")
    if meaning is not None:
        verdict = judge(c, text, meaning, heard)
        row["judge"] = verdict
        if verdict.get("verdict") != "PASS":
            row["fail"].append(f"meaning: {verdict.get('reason')}")
    return row, row["fail"]


def check_beat(c, plan_path: Path, meanings: list[str] | None, reroll: int) -> list[dict]:
    plan = json.loads(plan_path.read_text())
    rows = []
    for i, u in enumerate(plan["utterances"]):
        text, meaning = u["text"], meanings[i] if meanings else None
        wav = Path(u["wav"])
        row = {"beat": plan["beat"], "n": i + 1, "text": text, "takes": 1}
        res, fails = listen_once(c, text, wav, meaning)
        if fails and all(f.startswith(("round-trip", "meaning", "odd")) for f in fails):
            res2, fails2 = listen_once(c, text, wav, meaning)      # the transcriber's noise, or the audio's?
            if not fails2:
                res, fails = res2, fails2
        for k in range(reroll if fails else 0):
            plan = nb.reroll(plan_path, i)
            wav = Path(plan["utterances"][i]["wav"])
            row["takes"] += 1
            res, fails = listen_once(c, text, wav, meaning)
            if not fails:
                break
        row.update(res)
        row["fail"] = fails
        mark = "FAIL" if fails else "ok  "
        takes = f" (take {row['takes']})" if row["takes"] > 1 else ""
        print(f"{mark} {plan['beat']} #{i + 1}{takes}: {text}")
        for f in fails:
            print(f"       - {f}")
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plans", nargs="+")
    ap.add_argument("--meaning-dir", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--reroll", type=int, default=0, help="re-synthesise a failing utterance up to N times")
    a = ap.parse_args()
    c = client()
    rows = []
    for p in a.plans:
        plan = json.loads(Path(p).read_text())
        meanings = None
        if a.meaning_dir:
            mf = Path(a.meaning_dir) / f"{plan['beat']}.meaning"
            if mf.exists():
                meanings = [l.strip() for l in mf.read_text().splitlines() if l.strip()]
                if len(meanings) != len(plan["utterances"]):
                    sys.exit(f"FAIL: {mf} has {len(meanings)} lines, beat has {len(plan['utterances'])} utterances")
        rows += check_beat(c, Path(p), meanings, a.reroll)
    if a.report:
        Path(a.report).write_text(json.dumps(rows, indent=1) + "\n")
    bad = [r for r in rows if r["fail"]]
    print(f"{len(rows) - len(bad)}/{len(rows)} utterances pass")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
