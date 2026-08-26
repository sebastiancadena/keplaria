# Submission video — build record

What the submitted video is, byte for byte, and what produced it. The video
repository (`keplaria-video`, private) holds the take, the narration and the
assembly; this file is the public pointer to the exact artifact.

| Item | Value |
|---|---|
| File | `keplaria-video/dist/keplaria-submission.mp4` |
| sha256 | `767b89415780dde30dd29a22a31becc7698cc21b64539b2f1116c94ba6e82343` |
| `build/verify.sh` | `OK 205.966667s, -14.90 LUFS` (h264 1920x1080 30 fps yuv420p; AAC 48 kHz stereo; duration = sum of parts, ≤ 4:00; loudness in −15…−13 LUFS) |
| Live take | `takes/live_take_20260825_7.mkv`, recorded 2026-08-25 (take 7); its run is the cited evidence (45.1 s machine, 22.4 s human) |
| Frozen commit shown | `f972ce6` (`spikes/freeze/evidence.json`; deployed containers are tied to it by content) |
| Video repo commit | `91d7a56` |
| Narration | Google Cloud Text-to-Speech, `en-US-Chirp3-HD-Charon`, rendered by `video/narrate.sh` (see `THIRD_PARTY.md`) |
| YouTube URL | _(blank until uploaded; the one deliberate blank in the submission copy)_ |

## How it was checked

- `build/verify.sh` green on the file above.
- 41 audit frames (`build/frames.sh`, one every 5 s) read against the rule-12
  checklist in [video-audit.md](video-audit.md).
- A zero-context cold watch over the frames and narration answered the four
  rubric questions at 30 s and at full length (recorded in the audit file).

## Rebuilding it

`make vo && make scenes && make assemble && make verify` in the video repo;
`make vo` needs ADC for the `keplaria` project. The result is not
byte-identical across runs (TTS output varies), so the sha256 above names
the submitted file, not the recipe.
