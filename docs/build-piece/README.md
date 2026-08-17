# Build piece — "Tests That Prove Nothing"

The public content piece. Written 2026-08-17; **not yet published to a public
platform** — the platform decision is open.

## Status

- **Draft published privately** as an artifact for review:
  <https://claude.ai/code/artifact/ee1192be-a8e8-4ea0-8345-d1ec50f84cb8>
  Republishing the same file path from that conversation keeps the URL; from
  any other conversation, pass the URL as `url` or it creates a second
  artifact.
- **Platform not chosen.** dev.to was the recommendation (free, indexes fast,
  stable canonical URL for the submission); keplaria.com was rejected for now
  because `site/` does not exist and building it is unbudgeted.
- **The social post is drafted but unposted** — see below. It needs the
  article's public URL first, so it is blocked on the platform decision.

## Building

`article.src.html` is the source. `build.py` inlines the brand's three
vendored fonts from the sibling `keplaria-assets` repo and escapes every
non-ASCII character to a numeric entity, then writes `article.html`
(~1.55 MB, mostly font payload).

```bash
uv run python docs/build-piece/build.py
```

Two things the builder does that are not optional:

- **Fonts are inlined, never linked.** The artifact CSP blocks font CDNs, and
  a blocked webfont fails silently to a fallback — the page would still render,
  just not in the brand's faces, with nothing to tell you.
- **Non-ASCII becomes numeric entities.** Em dashes, curly apostrophes and the
  arrow rendered as mojibake when served without an explicit charset. Entities
  are charset-independent, so the text is correct wherever it lands rather than
  correct only where it was tested.

The built `article.html` is deliberately **not committed** — it is a
regenerable artifact dominated by ~1.2 MB of font bytes that already live in
`keplaria-assets`.

## Checks run before publishing

- Leak grep (the same pattern `scripts/doctor.sh` uses) plus greps for the
  project name, the ERP vendor, email addresses, and project numbers — all
  clean. The article names no private planning file, no risk id, and no
  infrastructure identifier.
- Both themes verified, fonts confirmed loaded, no horizontal overflow at
  380 px.

## Bonus-point requirements this satisfies

The piece carries an explicit hackathon-participation disclosure in its
closing section, alongside the synthetic-data statement and the AI-assistance
disclosure. Both are prerequisites, not decoration.

## Social post (drafted, not posted)

Needs `#AllThingsAgenticHackathon` and the article's public URL.

> The five bugs that mattered in my hackathon build were never red.
>
> A safety eval that only measured the model's manners. An approval system
> whose two halves were each correct and jointly inert. An audit that asked
> `> 1` when the answer was `>= 1`. A proof written to wait for an event the
> system cannot emit. A metric reading a field that's null on every record
> ever stored.
>
> All green. All wrong.
>
> Write-up: [link]
> #AllThingsAgenticHackathon
