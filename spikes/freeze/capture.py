"""Capture the frozen-artifact set: one commit, six artifacts, one file.

The submission needs versioned deployment, catalog, trace,
test, metric and billing artifacts captured FROM THE FROZEN COMMIT. Each of
those is produced somewhere already; what did not exist was the act of
tying them to one commit, so a reader gets a single frozen state instead of
six timestamps that may or may not describe the same code.

Everything is DISCOVERED, not declared:

- The commit is whatever HEAD is, and the tree must be clean. A `--expect`
  SHA turns "captured from HEAD" into "captured from the commit STATUS names".
- Nothing in the deploy path records a SHA (Cloud Build is handed a tarball
  of the working tree), so each Cloud Run image is pulled through the
  registry API and its files are compared, blob by blob, with `git ls-tree`
  at the commit. A file added, changed or removed after the build is drift
  and fails the section — including the ingress's missing `constraints.txt`
  when its image predates the pin.
- The engine image lives in a tenant project that refuses pulls, so the
  engine is checked the other way round: the graph's static import closure
  is walked from `app.agent`, and any commit after the engine's `updateTime`
  that touched a file in it (or `policy/`, `catalog/`, `pyproject.toml`,
  `uv.lock`) is drift.
- Traces are whatever Cloud Trace holds for the window in which this run
  drove `spikes/lifecycle/harness.py` against the deployment.
- Tests, evals and the billing export are re-run or re-read now, not copied
  from an earlier evidence file.

Run (both env files: the lifecycle harness and the evals need them):

    uv run --env-file .env --env-file .env.secrets \
        python spikes/freeze/capture.py --expect <sha>

`--skip-trace`, `--skip-tests`, `--skip-evals` leave a section recorded as
"not run", which makes the verdict INCOMPLETE rather than PASS.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import google.auth  # noqa: E402
import httpx  # noqa: E402
from google.auth.transport.requests import Request as AuthRequest  # noqa: E402

from spikes.freeze import provenance  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "keplaria")
REGION = "us-central1"
ENGINE_DISPLAY_NAME = "keplaria"
SERVICES = {
    "keplaria-console": ROOT / "console/Dockerfile",
    "keplaria-review": ROOT / "console/Dockerfile",
    "keplaria-ingress": ROOT / "ingress/Dockerfile",
}
GRAPH_ENTRY = "app.agent"
GRAPH_DATA = ("policy", "catalog", "pyproject.toml", "uv.lock")
EVAL_FLOOR = 0.9
BILLING_TABLE_PREFIX = "gcp_billing_export"


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def _token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(AuthRequest())
    return creds.token


def sh(*args: str, check: bool = True, env: dict | None = None) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, env=env)
    if check and proc.returncode:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {proc.stderr.strip()[-800:]}")
    return proc.stdout


# --- commit -------------------------------------------------------------------


def commit_section(expect: str | None) -> dict:
    sha = sh("git", "rev-parse", "HEAD").strip()
    dirty = [line for line in sh("git", "status", "--porcelain").splitlines()
             if not line.endswith("evidence.json")]
    subject = sh("git", "log", "-1", "--format=%s", sha).strip()
    date = sh("git", "log", "-1", "--format=%cI", sha).strip()
    problems = []
    if dirty:
        problems.append(f"working tree is not clean: {len(dirty)} path(s)")
    if expect and not sha.startswith(expect):
        problems.append(f"HEAD {sha[:7]} is not the expected {expect}")
    return {"sha": sha, "short": sha[:7], "subject": subject, "committed_at": date,
            "expected": expect, "clean": not dirty, "ok": not problems, "problems": problems}


def tree_at(sha: str) -> dict[str, str]:
    tree = {}
    for line in sh("git", "ls-tree", "-r", sha).splitlines():
        meta, path = line.split("\t", 1)
        tree[path] = meta.split()[2]
    return tree


# --- deployment: Cloud Run images, compared by content ------------------------


_MANIFEST_TYPES = ", ".join([
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
])


def image_files(image_ref: str, keep_prefix: str, token: str) -> dict[str, bytes]:
    """Every file under `keep_prefix` in the image, after layering."""
    repo, digest = image_ref.split("@", 1)
    host, _, name = repo.partition("/")
    base = f"https://{host}/v2/{name}"
    headers = {"Authorization": f"Bearer {token}", "Accept": _MANIFEST_TYPES}
    with httpx.Client(headers=headers, timeout=300, follow_redirects=True) as client:
        manifest = client.get(f"{base}/manifests/{digest}").raise_for_status().json()
        if "manifests" in manifest:  # an index: take the linux/amd64 image
            chosen = next(m for m in manifest["manifests"]
                          if m.get("platform", {}).get("architecture") == "amd64")
            manifest = client.get(f"{base}/manifests/{chosen['digest']}").raise_for_status().json()
        layers = []
        for layer in manifest["layers"]:
            blob = client.get(f"{base}/blobs/{layer['digest']}").raise_for_status().content
            files: dict[str, bytes] = {}
            with tarfile.open(fileobj=gzip.GzipFile(fileobj=io.BytesIO(blob)), mode="r|") as tar:
                for member in tar:
                    path = member.name.lstrip("./")
                    if not path.startswith(keep_prefix):
                        continue
                    if member.isfile():
                        files[path] = tar.extractfile(member).read()
                    elif "/.wh." in "/" + path:
                        files[path] = b""
            layers.append(files)
    return provenance.overlay(layers)


def cloud_run_section(tree: dict[str, str], token: str) -> dict:
    services, cache = {}, {}
    for service, dockerfile in SERVICES.items():
        desc = json.loads(sh("gcloud", "run", "services", "describe", service,
                             f"--region={REGION}", f"--project={PROJECT}", "--format=json"))
        revision = desc["status"]["latestReadyRevisionName"]
        rev = json.loads(sh("gcloud", "run", "revisions", "describe", revision,
                            f"--region={REGION}", f"--project={PROJECT}", "--format=json"))
        image = rev["spec"]["containers"][0]["image"]
        copies = provenance.copied_paths(dockerfile.read_text())
        if image not in cache:
            log(f"pulling {image.split('/')[-1][:40]}… for {service}")
            cache[image] = image_files(image, "srv/", token)
        comparison = provenance.compare(tree, cache[image], copies)
        services[service] = {
            "revision": revision,
            "deployed_at": rev["metadata"]["creationTimestamp"],
            "image": image,
            "url": desc["status"]["url"],
            "dockerfile": str(dockerfile.relative_to(ROOT)),
            "files_compared": len(comparison["matched"]) + len(comparison["mismatched"]),
            **{k: v for k, v in comparison.items() if k != "matched"},
        }
        log(f"{service}: {'matches' if comparison['ok'] else 'DRIFT'} "
            f"({len(comparison['matched'])} matched, {len(comparison['mismatched'])} changed, "
            f"{len(comparison['absent_from_image'])} absent, {len(comparison['extra_in_image'])} extra)")
    return services


# --- deployment: the engine, by import closure ---------------------------------


def engine_section(token: str) -> dict:
    url = f"https://{REGION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/{REGION}/reasoningEngines"
    body = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30).raise_for_status().json()
    engines = [e for e in body.get("reasoningEngines", []) if e.get("displayName") == ENGINE_DISPLAY_NAME]
    if len(engines) != 1:
        return {"ok": False, "problems": [f"expected one engine named {ENGINE_DISPLAY_NAME}, found {len(engines)}"]}
    engine = engines[0]
    spec = engine.get("spec", {}).get("deploymentSpec", {})
    closure = sorted(provenance.import_closure(ROOT, GRAPH_ENTRY))
    watched = closure + list(GRAPH_DATA)
    since = engine["updateTime"]
    drift = [line for line in sh("git", "log", f"--since={since}", "--format=%h %s", "--", *watched).splitlines()]
    return {
        "resource": engine["name"],
        "update_time": since,
        "min_instances": spec.get("minInstances"),
        "max_instances": spec.get("maxInstances"),
        "image_pullable": False,
        "how_verified": (
            "The engine image is pushed to a tenant-project registry that refuses pulls, "
            "so its content cannot be compared. Instead: every commit after update_time "
            "that touched a file the graph imports (static closure from app.agent) or a "
            "data directory it reads is listed as drift."
        ),
        "graph_closure_files": len(closure),
        "watched_paths": watched,
        "commits_touching_graph_since_deploy": drift,
        "ok": not drift,
    }


# --- catalog: Agent Registry ----------------------------------------------------


def catalog_section(engine_resource: str | None, token: str) -> dict:
    url = f"https://agentregistry.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/agents"
    response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if response.status_code != 200:
        return {"ok": False, "problems": [f"registry list returned HTTP {response.status_code}"]}
    agents = response.json().get("agents") or []
    engine_id = (engine_resource or "").rsplit("/", 1)[-1]
    entry = next((a for a in agents
                  if a.get("displayName") == ENGINE_DISPLAY_NAME and engine_id and engine_id in (a.get("agentId") or "")), None)
    return {
        "entries": len(agents),
        "entry": {k: entry.get(k) for k in ("name", "displayName", "agentId", "updateTime", "createTime")} if entry else None,
        "ok": entry is not None,
        "problems": [] if entry else [f"no registry entry named {ENGINE_DISPLAY_NAME} for engine {engine_id}"],
    }


# --- trace: a live run, then whatever Cloud Trace holds for its window ----------


def trace_section(token: str) -> dict:
    start = datetime.now(timezone.utc) - timedelta(seconds=5)
    log("driving spikes/lifecycle/harness.py against the deployment…")
    proc = subprocess.run([sys.executable, "spikes/lifecycle/harness.py"], capture_output=True, text=True, cwd=ROOT)
    end = datetime.now(timezone.utc) + timedelta(seconds=5)
    lifecycle = json.loads((ROOT / "spikes/lifecycle/evidence.json").read_text())
    time.sleep(60)  # trace ingestion lags the request by up to a minute
    params = {"startTime": start.isoformat().replace("+00:00", "Z"),
              "endTime": end.isoformat().replace("+00:00", "Z"), "pageSize": 200, "view": "ROOTSPAN"}
    body = httpx.get(f"https://cloudtrace.googleapis.com/v1/projects/{PROJECT}/traces",
                     params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60).raise_for_status().json()
    traces = [{"trace_id": t["traceId"], "root": t["spans"][0]["name"], "start": t["spans"][0]["startTime"]}
              for t in body.get("traces", [])]
    ok = proc.returncode == 0 and lifecycle.get("result") == "PASS" and bool(traces)
    return {
        "driver": "spikes/lifecycle/harness.py",
        "window": {"start": params["startTime"], "end": params["endTime"]},
        "lifecycle_result": lifecycle.get("result"),
        "case_id": lifecycle.get("case_id"),
        "traces": traces,
        "ok": ok,
        "problems": [] if ok else [f"lifecycle {lifecycle.get('result')}, rc={proc.returncode}, {len(traces)} trace(s): {proc.stdout[-300:]}"],
    }


# --- test ---------------------------------------------------------------------


def test_section() -> dict:
    log("running the unit + non-live integration suite…")
    proc = subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider"], capture_output=True, text=True, cwd=ROOT)
    counts = provenance.parse_pytest_summary(proc.stdout)
    live = subprocess.run(["uv", "run", "pytest", "-m", "live", "--collect-only", "-q"], capture_output=True, text=True, cwd=ROOT)
    selected = [ln for ln in live.stdout.splitlines() if "::" in ln]
    ok = proc.returncode == 0 and counts["failed"] == 0 and counts["errors"] == 0
    return {
        "command": "uv run pytest -q  (addopts: -m 'not live')",
        **counts,
        "live_marked_not_run": len(selected),
        "firestore": "emulator" if os.environ.get("FIRESTORE_EMULATOR_HOST") or _port_open(8451) else "keplaria-test",
        "ok": ok,
        "problems": [] if ok else [proc.stdout[-600:]],
    }


def _port_open(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("localhost", port)) == 0


# --- metric -------------------------------------------------------------------


def metric_section(engine_update_time: str | None) -> dict:
    log("re-running the domain evals (live Gemini)…")
    proc = subprocess.run(["bash", "tests/eval/run_domain_evals.sh"], capture_output=True, text=True, cwd=ROOT)
    evidence = json.loads((ROOT / "spikes/domain_evals/evidence.json").read_text())
    summary = next(m for m in evidence["summary_metrics"] if m["metric_name"] == "domain_case_pass")
    streak = json.loads((ROOT / "spikes/run_streak/evidence.json").read_text())
    streak_engine = streak.get("deployment", {}).get("engine", {}).get("update_time")
    streak_current = bool(engine_update_time) and streak_engine == engine_update_time
    ok = proc.returncode == 0 and summary["mean_score"] >= EVAL_FLOOR and summary["num_cases_error"] == 0
    problems = [] if ok else [f"evals rc={proc.returncode}, mean={summary['mean_score']}: {proc.stderr[-400:]}"]
    if not streak_current:
        problems.append(f"run_streak evidence was taken against engine update_time {streak_engine}, not {engine_update_time}")
    return {
        "domain_evals": {"command": "bash tests/eval/run_domain_evals.sh",
                         "graded_at": evidence["metadata"]["creation_timestamp"],
                         "cases": summary["num_cases_total"], "errors": summary["num_cases_error"],
                         "mean_score": summary["mean_score"], "floor": EVAL_FLOOR},
        "run_streak": {"evidence": "spikes/run_streak/evidence.json", "captured_at": streak.get("captured_at"),
                       "verdict": streak.get("verdict"), "engine_update_time": streak_engine,
                       "same_engine_as_frozen": streak_current},
        "ok": ok and streak_current,
        "problems": problems,
    }


# --- billing ------------------------------------------------------------------


def billing_section() -> dict:
    tables = json.loads(sh("bq", "ls", "--format=json", f"--project_id={PROJECT}", "billing_export"))
    names = [t["tableReference"]["tableId"] for t in tables if t["tableReference"]["tableId"].startswith(BILLING_TABLE_PREFIX)]
    if not names:
        return {"ok": False, "problems": ["no billing export table in keplaria.billing_export"]}
    table = names[0]
    query = (f"SELECT CAST(MIN(usage_start_time) AS STRING) first_usage, CAST(MAX(usage_start_time) AS STRING) last_usage, "
             f"CAST(MAX(export_time) AS STRING) last_export, COUNT(*) n_rows, ROUND(SUM(cost),2) cost_usd, "
             f"ROUND(SUM((SELECT IFNULL(SUM(c.amount),0) FROM UNNEST(credits) c)),2) credits_usd "
             f"FROM `{PROJECT}.billing_export.{table}`")
    row = json.loads(sh("bq", "query", "--format=json", "--use_legacy_sql=false", f"--project_id={PROJECT}", query))[0]
    row = {k: (int(v) if k == "n_rows" else float(v) if k.endswith("_usd") else v) for k, v in row.items()}
    ok = row["n_rows"] > 0
    return {"table": f"{PROJECT}.billing_export.{table}", "forward_only_since": row["first_usage"], **row,
            "gross_cost_usd": row["cost_usd"], "net_cost_usd": round(row["cost_usd"] + row["credits_usd"], 2),
            "ok": ok, "problems": [] if ok else ["export holds no rows yet"]}


# --- main ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", help="the SHA (prefix) STATUS names as the frozen commit")
    parser.add_argument("--skip-trace", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-evals", action="store_true")
    args = parser.parse_args()

    started = datetime.now(timezone.utc).isoformat()
    token = _token()
    commit = commit_section(args.expect)
    log(f"commit {commit['short']} — {commit['subject']}" + ("" if commit["ok"] else f" — {commit['problems']}"))
    tree = tree_at(commit["sha"])
    services = cloud_run_section(tree, token)
    engine = engine_section(token)
    log(f"engine: {'matches' if engine['ok'] else 'DRIFT'} — {len(engine.get('commits_touching_graph_since_deploy', []))} commit(s) touched the graph since {engine.get('update_time')}")
    catalog = catalog_section(engine.get("resource"), token)
    log(f"catalog: {'listed' if catalog['ok'] else catalog['problems']}")
    billing = billing_section()
    log(f"billing: {billing.get("n_rows")} rows, {billing.get('first_usage')} → {billing.get('last_usage')}")
    skipped = {"ok": None, "problems": ["not run (--skip)"]}
    trace = skipped if args.skip_trace else trace_section(token)
    tests = skipped if args.skip_tests else test_section()
    metric = skipped if args.skip_evals else metric_section(engine.get("update_time"))

    sections = {"commit": commit, "deployment": {"cloud_run": services, "engine": engine},
                "catalog": catalog, "trace": trace, "test": tests, "metric": metric, "billing": billing}
    flags = [commit["ok"], *(s["ok"] for s in services.values()), engine["ok"], catalog["ok"],
             trace["ok"], tests["ok"], metric["ok"], billing["ok"]]
    verdict = "INCOMPLETE" if None in flags else ("PASS" if all(flags) else "FAIL")
    problems = {name: sec["problems"] for name, sec in
                [("commit", commit), *services.items(), ("engine", engine), ("catalog", catalog),
                 ("trace", trace), ("test", tests), ("metric", metric), ("billing", billing)]
                if sec.get("problems")}
    for name, section in services.items():
        if not section["ok"]:
            problems[name] = [f"{len(section['mismatched'])} changed, {len(section['absent_from_image'])} absent, "
                              f"{len(section['extra_in_image'])} extra vs {commit['short']}"]

    evidence = {
        "captured_at": started,
        "what_this_is": ("The frozen-artifact set the submission cites: deployment, catalog, trace, test, metric and "
                         "billing artifacts, each discovered live and tied to the one commit in `commit`."),
        "verdict": verdict,
        "problems": problems,
        **sections,
    }
    path = HERE / "evidence.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n")
    log(f"{verdict} — evidence at {path.relative_to(ROOT)}")
    for name, why in problems.items():
        log(f"  {name}: {why[0][:200]}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
