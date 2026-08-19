"""Does gemini-3.7-flash honour a thinking budget as a BOUND rather than an off switch?

That question, and only that question, is why 3.7 was evaluated. On 3.6 a named
budget does not truncate reasoning at the number -- it stops the model reasoning
at length at all, which is why all three agents currently run with reasoning
effectively off and no measured margin on hard cases. If 3.7 honoured the budget
as a bound, that margin would come back at bounded latency.

Two probes, both re-runnable:

  matrix   -- thought-token counts across model x budget x response-schema, on
              the extractor's own prompt and document. The schema axis is not
              decoration: the deployed agents all carry an output_schema, and
              the behaviour differs sharply with and without one.
  throttle -- interleaved small calls to both models in the same minute, to
              tell a model-specific capacity limit apart from project load.

Run:
    set -a && source .env && set +a
    GOOGLE_CLOUD_LOCATION=global uv run python spikes/gemini_37_eval/probe.py matrix
    GOOGLE_CLOUD_LOCATION=global uv run python spikes/gemini_37_eval/probe.py throttle
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from google import genai
from google.genai import types

from app.schemas import EvidenceResult

MODELS = ("gemini-3.6-flash", "gemini-3.7-flash")
REPO = Path(__file__).resolve().parents[2]

client = genai.Client(vertexai=True, project="keplaria", location="global")


def extractor_prompt() -> str:
    """The evidence agent's own instruction, over its own fixture document."""
    doc = json.loads((REPO / "fixtures/documents/andes-verde-cert-2027.json").read_text())
    pages = doc.get("pages") or doc.get("redacted_pages") or doc
    return (
        "You extract corporate fields from a supplier document.\n\n"
        f"Document checksum: {doc.get('checksum', 'sha256:unknown')}\n\n"
        "Document pages, each labeled with its zero-based index:\n"
        f"{json.dumps(pages, ensure_ascii=False)}\n\n"
        "Extract every field you can support, and for each one return the verbatim "
        "span of page text the value came from. Copy the checksum exactly. Every "
        "value MUST appear inside the span you cite. Extract 'certificate_expiry' "
        "as an ISO date. If a field is not in the document, omit it. Never guess."
    )


def call(model: str, budget: int | None, schema: bool, prompt: str) -> dict:
    cfg = types.GenerateContentConfig(temperature=0.0)
    if schema:
        cfg.response_mime_type = "application/json"
        cfg.response_schema = EvidenceResult
    if budget is not None:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=budget)
    # 3.7 429s under this payload; retry so a capacity blip does not read as a
    # measurement. A cell that exhausts its retries is recorded, not dropped.
    for attempt in range(6):
        try:
            t0 = time.time()
            r = client.models.generate_content(model=model, contents=prompt, config=cfg)
            u = r.usage_metadata
            return {
                "thoughts": getattr(u, "thoughts_token_count", None) or 0,
                "seconds": round(time.time() - t0, 2),
            }
        except Exception as e:  # noqa: BLE001 -- the error class IS the finding
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                time.sleep(20 * (attempt + 1))
                continue
            raise
    return {"thoughts": None, "seconds": None, "error": "429 after 6 attempts"}


def matrix(n: int = 3) -> list[dict]:
    prompt = extractor_prompt()
    out = []
    for model in MODELS:
        for budget, schema in ((None, True), (1024, True), (1024, False)):
            rows = [call(model, budget, schema, prompt) for _ in range(n)]
            th = [r["thoughts"] for r in rows if r.get("thoughts") is not None]
            rec = {
                "model": model, "budget": budget, "schema": schema, "rows": rows,
                "thoughts": th,
                "thoughts_median": statistics.median(th) if th else None,
            }
            out.append(rec)
            print(json.dumps(rec), flush=True)
    print("\n=== thought tokens: budget as bound, or as off switch? ===")
    for r in out:
        print(f"{r['model']:<18} budget={str(r['budget']):<5} schema={str(r['schema']):<5} "
              f"median={r['thoughts_median']}  {r['thoughts']}")
    return out


def throttle(n: int = 12) -> dict:
    cfg = types.GenerateContentConfig(
        temperature=0.0, max_output_tokens=32,
        thinking_config=types.ThinkingConfig(thinking_budget=512),
    )
    res = {m: {"ok": 0, "429": 0, "other": 0} for m in MODELS}
    for _ in range(n):
        for m in MODELS:
            try:
                client.models.generate_content(
                    model=m, contents="Reply with the single word OK.", config=cfg)
                res[m]["ok"] += 1
            except Exception as e:  # noqa: BLE001
                key = "429" if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) else "other"
                res[m][key] += 1
    for m, r in res.items():
        print(f"{m}: {r['ok']}/{n} ok, {r['429']} x 429, {r['other']} other")
    return res


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "matrix"
    {"matrix": matrix, "throttle": throttle}[which]()
