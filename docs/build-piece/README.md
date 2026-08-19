# Build piece — "The smoke test said yes"

The public content piece. **Rewritten 2026-08-19 (day 8)**: the previous
defect-taxonomy walkthrough ("The hold landed before I approved it") was judged
too generic to publish; this piece replaces it with the three measured findings
that are actually novel, all repo-side spike evidence:

1. **Model Armor's prompt-injection filter misses the real fixture** the
   sentence-level heuristic taints with 5 findings — mechanism measured as
   context dilution with a boundary at 89/108 chars of prepended prose
   (`spikes/model_armor/evidence.json`).
2. **A named thinking budget is an off switch when an output_schema is in
   play** — 0 thinking tokens on both 3.6 and 3.7; drop the schema and 3.6
   reasons at a ~514-token median (`spikes/gemini_37_eval/evidence.json`).
3. **A shallow smoke test would have shipped 3.7**: 12/12 on interleaved small
   calls, then two 429 aborts on the real 8-case suite. Serving capacity, not
   quota (same evidence file).

Both prior drafts live in git history. Disclosure block, claim wording rules,
and the build pipeline are unchanged.

## Status

- **PUBLISHED 2026-08-19** on dev.to:
  <https://dev.to/sebastiancadena7/the-smoke-test-said-yes-1068>
  (article id 4437730; posted via the API with `DEVTO_API_KEY` from `.env` —
  see `.env.example`; source of truth for the body is `article.dev.md`, edit
  there and PUT the same id to update). Rendering verified after publish:
  title, both tables, blockquotes, disclosure all correct. This earns the
  +0.2 article bonus.
- **Draft also published privately** as an artifact (the styled HTML edition):
  <https://claude.ai/code/artifact/ee1192be-a8e8-4ea0-8345-d1ec50f84cb8>
  (pass the URL as `url` when republishing from a new conversation or it
  creates a second artifact).
- **The social post is drafted but DEFERRED (2026-08-19)** — the user wants to
  re-review the message before promoting it. Do not post it on their behalf.
  URL is already filled in below; posting it on X/LinkedIn with the hashtag
  earns the remaining +0.2.

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
  project name, the ERP vendor, email addresses, infrastructure identifiers,
  and project numbers — all clean. The article names no private planning file,
  no risk id, no supplier fixture name, and no region or address. It does name
  Model Armor and the two Gemini model ids, which the findings require to be
  reproducible; both appear in committed repo evidence.
- Both themes verified via headless chromium at 380 px: no horizontal
  overflow, all three brand faces loaded, correct token grounds in light and
  dark.

## Claim discipline carried into the piece

- The sentence-level scanner is described as "a heuristic over a
  representative fixture, not a general injection defence"; the win over the
  commercial filter is scoped to "the corpus that actually exists".
- The off-switch claim is scoped to the call shape (schema + budget), exactly
  as `spikes/gemini_37_eval/evidence.json` refines it — never as a bare
  property of the model.
- The 3.7 rejection is reported as closed history; the 429s are attributed to
  serving capacity with the quota-parity evidence, not to a raisable limit.
- Model Armor's malicious-URI filter is credited as working; the piece is a
  measurement, not a takedown.

## Bonus-point requirements this satisfies

The piece carries an explicit hackathon-participation disclosure in its
closing section, alongside the synthetic-data statement and the AI-assistance
disclosure. Both are prerequisites, not decoration.

Checked against the live rules page 2026-08-19: the **article** needs no
hashtag — its requirement is stating the content was *"created for the
purposes of entering this hackathon"*, which the disclosure now says
literally (do not paraphrase it away in edits). The
`#AllThingsAgenticHackathon` hashtag is required on the **social post**
(X/LinkedIn), not here.

## Social post (drafted, not posted)

Needs `#AllThingsAgenticHackathon` and the article's public URL.

> A commercial prompt-injection filter, at its most sensitive setting,
> returned NO_MATCH_FOUND on the injected document a small sentence-level
> heuristic catches with 5 findings.
>
> Not language. Not segmentation. Context dilution — the payload is detected
> under 89 characters of surrounding prose and gone at 108.
>
> That's one of three findings from my hackathon build where the surface
> signal said yes and a small probe said no:
>
> — a documented "thinking budget" that is actually an off switch when
> structured output is on: 0 reasoning tokens, both model generations
> — a model that went 12/12 on small calls, then 429'd twice on the real
> workload. Capacity, not quota.
>
> Enabled is not detecting. Configured is not bounded. Responding is not
> serving.
>
> Write-up: <https://dev.to/sebastiancadena7/the-smoke-test-said-yes-1068>
> #AllThingsAgenticHackathon
