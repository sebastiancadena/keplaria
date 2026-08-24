"""Does an open-weights Gemma produce GROUNDED extractions on this project's own documents?

That question, and only that question. The Stage Three bonus rewards an
additional Google model shipped and measured; this measures one before anything
ships, so the decision to ship rests on numbers rather than on hope.

What makes this a measurement of THIS system rather than of a paraphrase of it:
the prompt is not rewritten here. `app.agent.evidence_agent.instruction` is the
deployed instruction string, `app.documents.load_document` builds the same
redacted derivative the graph builds, and `app.nodes._format_pages` renders the
page markers the model actually sees. The two state-template placeholders are
substituted the way ADK's inject_session_state substitutes them. Edit the
deployed instruction and this probe measures the new one.

Grading is `app.grounding.validate` itself -- the same function the graph calls
on the same shape of result. Nothing about the comparison is reimplemented, so
nothing about it can drift from production semantics.

THE METRIC TRAP, AND WHY THE HEADLINE NUMBER IS NOT "GROUNDED".
`validate` returns grounded=True for an EMPTY field list: extracting nothing is
trivially supported by the document. So a grounded-rate on its own is a check
that cannot fail, and a model that returns `{"fields": []}` would top the table.
The headline is therefore `useful` -- grounded AND carrying a correct
`certificate_expiry`, which is the only extracted field `app.lifecycle` consumes
(EXPIRY_FIELD). Field counts and names are recorded beside it so an empty
extraction is visible as an empty extraction.

Two prompt cells per model, recorded separately and never silently swapped:

  as_shipped   -- the deployed instruction, unaltered.
  field_named  -- the same instruction plus one paragraph naming the target
                  fields. If a model only performs under an adapted prompt,
                  the table has to say so rather than quietly adopt the
                  adaptation and report the better number.

Sequential calls only. The candidate 429s at two concurrent requests, so a cell
that exhausts its retries is RECORDED, not dropped -- the throttle behaviour is
a finding, exactly as it was for gemini-3.7-flash.

Run (the global endpoint is where these models are served; .env carries
us-central1 for the engine):

    GOOGLE_CLOUD_LOCATION=global uv run --env-file .env \
        python spikes/gemma_extraction/probe.py

    # adds the local open-weights column from Ollama on the dev host
    GOOGLE_CLOUD_LOCATION=global uv run --env-file .env \
        python spikes/gemma_extraction/probe.py --with-ollama

Writes spikes/gemma_extraction/evidence.json. It writes NOTHING ELSE: no
existing evidence file is touched, no deployed state is read or changed, and
nothing under app/ is modified. A harness that rewrites another harness's
evidence has unproven a passing gate in this repo before.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from app.agent import evidence_agent  # noqa: E402
from app.documents import load_document  # noqa: E402
from app.grounding import validate as grounding_validate  # noqa: E402
from app.lifecycle import EXPIRY_FIELD  # noqa: E402
# Private, and imported deliberately: the page-marker format is part of what
# the model is being asked to read. Reproducing it here would measure a
# different prompt the first time either copy changed.
from app.nodes import _format_pages  # noqa: E402
from app.schemas import EvidenceResult  # noqa: E402

VERTEX_LOCATION = "global"

# The verdict lives in the source, not only in the output, because running a
# harness in this repo REWRITES its evidence.json -- a conclusion kept only in
# the artifact would be silently replaced by the next run.
VERDICT = (
    "REJECT for the deployed extraction path; the content finding is the "
    "reportable result. gemma-4-26b-a4b-it-maas extracted the correct values "
    "with correct verbatim spans on 6 of 6 documents (useful_after_"
    "normalisation 6/6, both prompt cells), and honoured the response_schema "
    "on 0 of 6 (schema_conformant 0/6, both cells). It abandoned the schema in "
    "three distinct shapes across otherwise identical temperature-0 calls: a "
    "fields list keyed field_name/page_index with no confidence; a fields "
    "OBJECT keyed by field name; and no fields key at all, every field hoisted "
    "to the top level. Since every agent in this graph carries an "
    "output_schema, a producer whose JSON shape varies between identical calls "
    "cannot be an LlmAgent here without a tolerant adapter in deployed code. "
    "The same open weights served locally by Ollama scored 6/6 useful and 6/6 "
    "schema_conformant, which locates the defect in structured-output "
    "enforcement on the serving path rather than in the model's reading of the "
    "document. Latency is the secondary reason: ~9-11s per call against the "
    "deployed extractor's ~2.4s, inside a 2:10 run budget."
)

CONTROL = "gemini-3.6-flash"
CANDIDATE = "gemma-4-26b-a4b-it-maas"
OLLAMA_MODEL = "gemma4:31b"
OLLAMA_URL = "http://localhost:11434/api/chat"

# Each document, what it exercises, and the expiry a correct extraction finds.
# `expiry: None` means the correct behaviour is to extract NO expiry at all --
# the instruction's "if a field is not in the document, omit it. Never guess."
DOCUMENTS = [
    ("fixture:andes-verde-cert-2027", "ordinary certificate", "2027-01-01"),
    ("fixture:andes-verde-cert-2028", "renewal of the same supplier", "2028-01-01"),
    ("fixture:boilerplate-cert-clean", "longer boilerplate around one date", "2028-06-15"),
    ("fixture:rio-claro-cert-2027", "second supplier, terser document", "2027-03-15"),
    ("fixture:sierra-nevada-cert-noexpiry", "NO expiry present: never-guess case", None),
    (
        "fixture:manglar-cert-injected",
        "carries a planted instruction and a decoy far-future date; production "
        "blocks this document before any model sees it, so this cell measures "
        "model behaviour only and claims nothing about the deployed gate",
        "2027-06-30",
    ),
]

FIELD_NAMING = (
    "\n\nThe fields worth looking for in a document of this kind are: "
    "legal_name, registration_number, certificate_expiry, issuer. Extract "
    "each one only if the document supports it."
)


def build_prompt(ref: str, cell: str) -> tuple[str, object]:
    """The deployed instruction, with its two state placeholders resolved."""
    derivative = load_document(ref)
    prompt = evidence_agent.instruction.replace(
        "{document_checksum}", derivative.checksum
    ).replace("{document_pages}", _format_pages(derivative.pages))
    if cell == "field_named":
        prompt += FIELD_NAMING
    elif cell != "as_shipped":
        raise ValueError(f"unknown cell: {cell!r}")
    return prompt, derivative


def _deployed_config(thinking: bool) -> types.GenerateContentConfig:
    """The evidence agent's own generation config, mirrored.

    `thinking=False` is the documented fallback for a model that rejects
    thinking_config outright; taking it is recorded on the row, never silent.
    """
    cfg = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=EvidenceResult,
    )
    if thinking:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=1024)
    return cfg


def call_vertex(client, model: str, prompt: str) -> dict:
    """One extraction. Returns the parsed result plus how it was obtained."""
    thinking = True
    for attempt in range(6):
        try:
            t0 = time.time()
            resp = client.models.generate_content(
                model=model, contents=prompt, config=_deployed_config(thinking)
            )
            seconds = round(time.time() - t0, 2)
            raw = resp.text or ""
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError) as exc:
                return {
                    "result": None,
                    "seconds": seconds,
                    "error": f"unparseable response: {exc}",
                    "thinking_config": thinking,
                    "raw": raw,
                }
            return {
                "result": parsed,
                "seconds": seconds,
                "error": None,
                "thinking_config": thinking,
                "raw": raw,
            }
        except Exception as exc:  # noqa: BLE001 -- the error class IS the finding
            text = str(exc)
            if thinking and ("thinking" in text.lower() or "thought" in text.lower()):
                # The candidate may not accept the deployed thinking budget.
                # Retry once without it and say so on the row.
                thinking = False
                continue
            if "429" in text or "RESOURCE_EXHAUSTED" in text:
                time.sleep(20 * (attempt + 1))
                continue
            return {
                "result": None,
                "seconds": None,
                "error": text[:400],
                "thinking_config": thinking,
                "raw": "",
            }
    return {
        "result": None,
        "seconds": None,
        "error": "429 after 6 attempts",
        "thinking_config": thinking,
        "raw": "",
    }


def call_ollama(model: str, prompt: str) -> dict:
    """The same prompt against local open weights on the dev host.

    Author hardware, not a cloud claim, and labelled that way everywhere it is
    reported.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": EvidenceResult.model_json_schema(),
        "options": {"temperature": 0.0},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=900) as fh:
            body = json.loads(fh.read().decode("utf-8"))
        seconds = round(time.time() - t0, 2)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {
            "result": None, "seconds": None, "error": str(exc)[:400],
            "thinking_config": None, "raw": "",
        }

    content = (body.get("message") or {}).get("content") or ""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError) as exc:
        return {
            "result": None,
            "seconds": seconds,
            "error": f"unparseable response: {exc}",
            "thinking_config": None,
            "raw": content,
        }
    return {
        "result": parsed, "seconds": seconds, "error": None,
        "thinking_config": None, "raw": content,
    }


# Key aliases observed in the wild. The normaliser below RENAMES keys and
# supplies a confidence the validator requires; it never invents a value, a
# span or a page. Everything it did is listed on the row, so a reader can see
# exactly how much help a result needed before it graded.
_NAME_KEYS = ("name", "field_name", "field")
_PAGE_KEYS = ("page", "page_index", "page_number")


def normalise(result: object) -> tuple[object, list[str]]:
    """Map a near-miss result onto the deployed schema, reporting every step.

    This exists because a model can read a document correctly and still fail
    `validate` purely on key names. Conflating those two failures would report
    "cannot extract" for a model that extracted everything. The strict number
    is still the headline; this is the second column that says why it missed.
    """
    applied: list[str] = []
    if not isinstance(result, dict):
        return result, applied

    out = dict(result)
    fields = out.get("fields")

    # Third observed shape: no `fields` key at all -- each extracted field is
    # hoisted to the top level as `<field_name>: {value, span, page}`.
    if fields is None:
        hoisted = {
            k: v
            for k, v in out.items()
            if k != "document_checksum" and isinstance(v, dict) and "value" in v
        }
        if hoisted:
            applied.append("top_level_fields_to_list")
            for key in hoisted:
                out.pop(key, None)
            fields = {k: v for k, v in hoisted.items()}

    # Some configs return fields as an object keyed by field name.
    if isinstance(fields, dict):
        applied.append("fields_object_to_list")
        fields = [{**v, "name": k} for k, v in fields.items() if isinstance(v, dict)]

    if not isinstance(fields, list):
        return out, applied

    rebuilt = []
    for entry in fields:
        if not isinstance(entry, dict):
            rebuilt.append(entry)
            continue
        item = dict(entry)
        for key in _NAME_KEYS:
            if key in item:
                if key != "name":
                    applied.append(f"{key}->name")
                item["name"] = item.pop(key)
                break
        for key in _PAGE_KEYS:
            if key in item:
                if key != "page":
                    applied.append(f"{key}->page")
                item["page"] = item.pop(key)
                break
        if "confidence" not in item:
            # The validator requires a float in [0,1]; the model supplied none.
            # Supplying it is help, and it is recorded as help.
            applied.append("confidence_supplied_by_adapter")
            item["confidence"] = 1.0
        rebuilt.append(item)

    out["fields"] = rebuilt
    return out, sorted(set(applied))


def score(call: dict, derivative, expected_expiry: str | None) -> dict:
    """Grade one call with the deployed validator, then ask if it is USEFUL.

    `grounded` alone cannot fail on an empty extraction, so it is reported
    beside the field census and beside `useful`, which is the question the
    lifecycle actually asks of this agent.

    Every row is graded TWICE: strictly, exactly as the deployed graph would
    grade it, and again after `normalise` renames near-miss keys. The gap
    between the two is the whole difference between "this model cannot read
    the document" and "this model will not honour the response schema".
    """
    row = {
        "seconds": call["seconds"],
        "error": call["error"],
        "thinking_config": call["thinking_config"],
        "raw": (call.get("raw") or "")[:800],
    }
    result = call["result"]
    if result is None:
        row.update(
            grounded=False, reason="NO_RESULT", field_count=0, field_names=[],
            expiry_value=None, expiry_correct=False, useful=False,
            schema_conformant=False, normalisation=[],
            grounded_normalised=False, reason_normalised="NO_RESULT",
            useful_normalised=False,
        )
        return row

    def census(res: object) -> tuple[list, list, str | None]:
        fields = res.get("fields") if isinstance(res, dict) else None
        fields = fields if isinstance(fields, list) else []
        names = [f.get("name") for f in fields if isinstance(f, dict)]
        expiry = next(
            (
                f.get("value")
                for f in fields
                if isinstance(f, dict) and f.get("name") == EXPIRY_FIELD
            ),
            None,
        )
        return fields, names, expiry

    verdict = grounding_validate(result, derivative)
    fields, names, expiry = census(result)
    # For the never-guess document the correct expiry is no expiry at all.
    expiry_correct = (expiry == expected_expiry)

    normalised, applied = normalise(result)
    verdict_n = grounding_validate(normalised, derivative)
    _, names_n, expiry_n = census(normalised)
    expiry_correct_n = (expiry_n == expected_expiry)

    row.update(
        grounded=verdict.grounded,
        reason=verdict.reason,
        reason_field=verdict.field,
        field_count=len(fields),
        field_names=names,
        expiry_value=expiry,
        expiry_correct=expiry_correct,
        useful=bool(verdict.grounded and expiry_correct),
        # Did the model return the schema it was given, unaided?
        # An absent `fields` list needs no key renaming, so `not applied` on
        # its own would mark the most broken shape observed -- every field
        # hoisted to the top level -- as conformant. Requiring the list closes
        # that, the same way `useful` closes the empty-extraction hole above.
        schema_conformant=bool(
            not applied and isinstance(result.get("fields"), list)
        ),
        normalisation=applied,
        grounded_normalised=verdict_n.grounded,
        reason_normalised=verdict_n.reason,
        field_names_normalised=names_n,
        expiry_value_normalised=expiry_n,
        useful_normalised=bool(verdict_n.grounded and expiry_correct_n),
    )
    return row


def run(with_ollama: bool) -> dict:
    client = genai.Client(vertexai=True, project="keplaria", location=VERTEX_LOCATION)

    runners = [
        ("vertex", CONTROL, lambda m, p: call_vertex(client, m, p)),
        ("vertex", CANDIDATE, lambda m, p: call_vertex(client, m, p)),
    ]
    if with_ollama:
        runners.append(("ollama-dev-host", OLLAMA_MODEL, call_ollama))

    rows = []
    for host, model, invoke in runners:
        for cell in ("as_shipped", "field_named"):
            for ref, exercises, expected in DOCUMENTS:
                prompt, derivative = build_prompt(ref, cell)
                graded = score(invoke(model, prompt), derivative, expected)
                row = {
                    "host": host,
                    "model": model,
                    "cell": cell,
                    "document": ref,
                    "exercises": exercises,
                    "expected_expiry": expected,
                    **graded,
                }
                rows.append(row)
                print(
                    f"{model:<24} {cell:<12} {ref.split(':')[1]:<32} "
                    f"useful={str(row['useful']):<5} "
                    f"schema_ok={str(row['schema_conformant']):<5} "
                    f"useful_norm={str(row['useful_normalised']):<5} "
                    f"fields={row['field_count']} reason={row['reason'] or '-'} "
                    f"{row['seconds']}s",
                    flush=True,
                )
    return summarise(rows)


def summarise(rows: list[dict]) -> dict:
    cells = {}
    for row in rows:
        key = f"{row['model']}::{row['cell']}"
        cells.setdefault(
            key,
            {
                "host": row["host"],
                "model": row["model"],
                "cell": row["cell"],
                "documents": 0,
                "useful": 0,
                "grounded": 0,
                "schema_conformant": 0,
                "useful_after_normalisation": 0,
                "empty_extractions": 0,
                "errors": 0,
                "seconds": [],
                "failure_reasons": {},
                "normalisations_needed": {},
            },
        )
        cell = cells[key]
        cell["documents"] += 1
        cell["useful"] += int(row["useful"])
        cell["grounded"] += int(row["grounded"])
        cell["schema_conformant"] += int(row["schema_conformant"])
        cell["useful_after_normalisation"] += int(row["useful_normalised"])
        cell["empty_extractions"] += int(row["field_count"] == 0)
        for step in row.get("normalisation") or []:
            cell["normalisations_needed"][step] = (
                cell["normalisations_needed"].get(step, 0) + 1
            )
        cell["errors"] += int(bool(row["error"]))
        if row["seconds"] is not None:
            cell["seconds"].append(row["seconds"])
        if not row["useful"]:
            reason = row["reason"] or "NOT_USEFUL_BUT_GROUNDED"
            cell["failure_reasons"][reason] = cell["failure_reasons"].get(reason, 0) + 1

    for cell in cells.values():
        secs = cell.pop("seconds")
        cell["seconds_median"] = round(statistics.median(secs), 2) if secs else None
        cell["seconds_max"] = max(secs) if secs else None

    print("\n=== useful / documents (grounded AND correct certificate_expiry) ===")
    print("    useful      = as the deployed graph would grade it, unaided")
    print("    schema_ok   = returned the response_schema it was given")
    print("    useful_norm = same content after key renaming only\n")
    for key, cell in cells.items():
        print(
            f"{key:<44} useful={cell['useful']}/{cell['documents']}  "
            f"schema_ok={cell['schema_conformant']}/{cell['documents']}  "
            f"useful_norm={cell['useful_after_normalisation']}/{cell['documents']}  "
            f"empty={cell['empty_extractions']}  "
            f"median={cell['seconds_median']}s  {cell['failure_reasons']}"
        )
    return {"cells": cells, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-ollama",
        action="store_true",
        help="add the local open-weights column from Ollama on the dev host",
    )
    args = parser.parse_args()

    measured = run(args.with_ollama)
    out = Path(__file__).resolve().parent / "evidence.json"
    out.write_text(
        json.dumps(
            {
                "spike": "gemma_extraction",
                "ran": time.strftime("%Y-%m-%d %H:%M:%S"),
                "verdict": VERDICT,
                "question": (
                    "Does an open-weights Gemma produce grounded, USEFUL "
                    "extractions on this project's own documents, under the "
                    "deployed instruction and the deployed validator?"
                ),
                "reproduce": (
                    "GOOGLE_CLOUD_LOCATION=global uv run --env-file .env python "
                    "spikes/gemma_extraction/probe.py"
                    + (" --with-ollama" if args.with_ollama else "")
                ),
                "metric_note": (
                    "app.grounding.validate returns grounded=True for an empty "
                    "field list, so grounded-rate alone is a check that cannot "
                    "fail. The headline is `useful`: grounded AND a correct "
                    "certificate_expiry, the only extracted field app.lifecycle "
                    "consumes. For sierra-nevada-cert-noexpiry the correct "
                    "expiry is None -- extracting one is a failure, not a find."
                ),
                "conformance_note": (
                    "Every row is graded twice. `useful` is the strict grade: "
                    "exactly what the deployed graph would make of the result, "
                    "unaided. `useful_after_normalisation` re-grades the same "
                    "content after an adapter RENAMES near-miss keys "
                    "(field_name->name, page_index->page, a fields object to a "
                    "fields list) and supplies the confidence the validator "
                    "requires. The adapter never invents a value, a span or a "
                    "page, and every step it took is listed per row under "
                    "`normalisation`. A large gap between the two columns means "
                    "the model read the document correctly and would not honour "
                    "the response schema -- a different finding from being "
                    "unable to extract, and it must be reported as such."
                ),
                "prompt_note": (
                    "as_shipped is app.agent.evidence_agent.instruction "
                    "unaltered. field_named appends one paragraph naming target "
                    "fields; it is reported as its own cell and never merged "
                    "into the as_shipped number."
                ),
                "hardware_note": (
                    "host=ollama-dev-host rows ran on the author's DGX Spark "
                    "workstation, not on Google Cloud. They are an "
                    "open-weights data point and carry no deployment claim."
                ),
                "deployed_state_note": (
                    "Nothing was deployed, redeployed or modified to produce "
                    "this file. No app/ source, no engine, no ingress, no "
                    "service. The ten-run streak and every other committed "
                    "evidence file are untouched."
                ),
                **measured,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
