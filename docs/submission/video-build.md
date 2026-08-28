# Submission video — build record

What the submitted video is, byte for byte, and what produced it. The video
repository (`keplaria-video`, private) holds the take, the narration and the
assembly; this file is the public pointer to the exact artifact.

| Item | Value |
|---|---|
| File | `keplaria-video/dist/keplaria-submission.mp4` |
| sha256 | `838507b1588e058ebef92069eb9c3c1bd38229f3fdd39b0d1b92418d68869c59` (re-narrated 2026-08-27; the delivered 2026-08-26 file was `b2f46eda…`, 3:23) |
| `build/verify.sh` | `OK 222.766667s, -14.86 LUFS` (h264 1920x1080 30 fps yuv420p; AAC 48 kHz stereo; duration = sum of parts, ≤ 4:00; loudness in −15…−13 LUFS) |
| Live take | `takes/live_take_20260825_7.mkv`, recorded 2026-08-25 (take 7); its run is the cited evidence (45.1 s machine, 22.4 s human) |
| Frozen commit shown | `f972ce6` (`spikes/freeze/evidence.json`; deployed containers are tied to it by content) |
| Video repo commit | `2c01b8a` |
| Narration | Google Cloud Text-to-Speech, `en-US-Chirp3-HD-Charon` (see `THIRD_PARTY.md`), one request per sentence through `video/narrate_beat.py`; every utterance passed the three listeners in `video/listen.py` (round-trip transcription, pitch contour, blind meaning), 55/55, six after a re-roll |
| YouTube URL | <https://youtu.be/54GiU75AjH4> (public, uploaded 2026-08-26; it still carries the `b2f46eda…` cut until the re-narrated file is uploaded and every surface is re-pointed) |

## How it was checked

- `build/verify.sh` green on the file above.
- 41 audit frames (`build/frames.sh`, one every 5 s) read against the rule-12
  checklist in [video-audit.md](video-audit.md).
- A zero-context cold watch over the frames and narration answered the four
  rubric questions at 30 s and at full length (recorded in the audit file).

## Rebuilding it

`make vo && make listen && make scenes && make assemble && make verify` in
the video repo; `make vo` and `make listen` need ADC for the `keplaria`
project. The result is not byte-identical across runs (TTS output varies,
and `make listen` re-rolls a take the listeners reject), so the sha256 above
names the built file, not the recipe; the validated per-sentence takes are
committed in the video repo (`vo/utt/`) and `python3 build/vo.py --replace`
rebuilds the beats from them without a TTS request.
