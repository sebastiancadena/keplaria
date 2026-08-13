#!/usr/bin/env python3
"""Verify the yente screening stack. Run FROM THE VM (stdlib only).

Checks the service is healthy, that only the synthetic fixture dataset is
indexed, and that /match discriminates: a sanctioned supplier hits, a
near-name decoy is surfaced rather than silently collapsed, and a clean
supplier produces no match.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
DS = "keplaria_synthetic"

failures = []


def report(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


def match(props, schema="Company", cutoff=None, threshold=None):
    """POST /match. `cutoff` is the score below which yente drops a result
    entirely; `threshold` is the score at or above which it sets match=True.
    Both default server-side (0.5 / 0.7) — pass cutoff explicitly to see the
    sub-threshold candidates a reviewer needs for false-positive handling."""
    qs = []
    if cutoff is not None:
        qs.append(f"cutoff={cutoff}")
    if threshold is not None:
        qs.append(f"threshold={threshold}")
    url = f"{BASE}/match/{DS}" + ("?" + "&".join(qs) if qs else "")
    body = json.dumps(
        {"queries": {"q": {"schema": schema, "properties": props}}}
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["responses"]["q"]["results"]


def show(results, n=4):
    for x in results[:n]:
        topics = x.get("properties", {}).get("topics")
        print(
            f"      {x['score']:.3f}  match={str(x['match']):<5} "
            f"{x['id']:<12} {x['caption']}  topics={topics}"
        )
    if not results:
        print("      (no results)")


print("=== 1. health ===")
try:
    get("/healthz")
    report("/healthz reachable", True)
except (urllib.error.URLError, OSError) as e:
    report("/healthz reachable", False, str(e))
    print("\nservice is down; aborting")
    sys.exit(1)

print("\n=== 2. catalog ===")
cat = get("/catalog")
names = [d["name"] for d in cat.get("datasets", [])]
print(f"      datasets: {names}")
report(f"only {DS} is indexed", names == [DS], f"got {names}")

print("\n=== 3. sanctioned company, near-exact name ===")
r = match({"name": ["Comercializadora Andes Verde SAS"], "country": ["co"]})
show(r)
report(
    "top hit is syn-co-001 and is flagged",
    bool(r) and r[0]["id"] == "syn-co-001" and r[0]["match"],
    f"top={r[0]['id'] if r else None}",
)

print("\n=== 4. fuzzy person: decoy ranks below the true hit ===")
q = {"name": ["Aurelio Betancourt Salgado"], "birthDate": ["1968-06-04"]}

r = match(q, schema="Person")
show(r)
report(
    "at default cutoff, only syn-pe-001 is returned",
    [x["id"] for x in r] == ["syn-pe-001"] and r[0]["match"],
    f"got {[x['id'] for x in r]}",
)

print("      -- same query at cutoff=0.0 --")
r = match(q, schema="Person", cutoff=0.0)
show(r)
by_id = {x["id"]: x for x in r}
report(
    "decoy syn-pe-006 surfaces as a sub-threshold candidate",
    "syn-pe-006" in by_id,
    f"got {sorted(by_id)}",
)
if {"syn-pe-001", "syn-pe-006"} <= set(by_id):
    report(
        "decoy scores strictly below the true hit",
        by_id["syn-pe-006"]["score"] < by_id["syn-pe-001"]["score"],
        f"{by_id['syn-pe-006']['score']:.3f} vs {by_id['syn-pe-001']['score']:.3f}",
    )
    report(
        "decoy is not auto-flagged as a match",
        not by_id["syn-pe-006"]["match"],
    )

print("\n=== 5. clean supplier, no match ===")
r = match({"name": ["Panaderia El Trigal Dorado Ltda"], "country": ["co"]})
show(r)
flagged = [x["id"] for x in r if x["match"]]
report("nothing flagged as a match", not flagged, f"flagged {flagged}")

print()
if failures:
    print(f"FAILED ({len(failures)}): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
