"""Thin vertical: end-to-end proof against deployed infrastructure.

Publishes one canonical event to the topic, waits for the case to reach a
terminal command, then republishes the SAME event and asserts the duplicate
was NOT reprocessed: the command still shows a single graph claim
(`attempts == 1` — that field counts claim_command acquisitions, not ERP call
attempts; the executor never increments it) and `case_version` has not moved.
No second ERP write follows from that plus two other mechanisms this script
relies on but does not measure directly: the executor skips DONE commands, and
the ERP rejects duplicate deterministic IDs. Claim this evidence as a
no-reprocessing proof with idempotent execution, not as a counted guarantee of
exactly one external POST. Writes evidence into this directory — never a
scratchpad.

Authentication is proven with two anonymous witnesses rather than `/healthz`:
that route returns 404 both anonymously and with a valid identity token
(something in front of the container intercepts it), so a 404 there proves
nothing about auth. `POST /pubsub/push` and `GET /` both go through the same
Cloud Run IAM check and must come back 403 to an anonymous caller.

Routing and screening are read off the case document, not engine session
state: app.nodes persists a compact summary onto `cases/{case_id}` at
execution time (see commit_commands / quarantine_case), which is what makes
those decisions inspectable from outside the engine.

SUPPLIER is deliberately a name that does NOT match any sanctioned entity in
fixtures/watchlist/entities.ftm.json. Screening still executes and still
proves yente reachability over the private network; it just returns no
match, which is the honest picture of a legitimate supplier being onboarded.
Screening was advisory when this spike ran; the risk gate that supersedes it
lands in spikes/policy_gate. This script is preserved as the day-3 artifact
and is not updated to the new behaviour. It must never be pointed at a
fixture entity: the resulting evidence.json would otherwise read as "a
sanctioned entity was onboarded and called success." Operationally: with the
gate now in place, if the screening service is unreachable the case parks as
`awaiting_approval` and no command is ever claimed, so `wait_for_command`
below times out and this script reads FAIL for a reason unrelated to what it
tests — the screening service must be confirmed reachable before re-running
this script, or a FAIL here says nothing about the thin-vertical path it was
written to prove.

Note: evidence.json also carries a top-level `trace_id` and a `traces` block
that this script does NOT write — they are merged in by hand after a run
from Cloud Trace lookups. A re-run of this script overwrites evidence.json
and drops those fields; re-merge them afterwards if they're needed, don't
fabricate replacements.

Run: uv run --env-file .env python spikes/thin_vertical/verify.py
"""

import json
import os
import subprocess
import sys
import time
import uuid

# Running this file directly (as opposed to `pytest`, which has pythonpath=["."]
# from pyproject.toml) puts spikes/thin_vertical/ on sys.path[0], not the repo
# root — add it so `app.*` resolves the same way it does under pytest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.cloud import pubsub_v1  # noqa: E402

from app.state.commands import DONE, get_command  # noqa: E402
from app.state.firestore import get_client  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "keplaria")
TOPIC = "keplaria-events"
EVIDENCE_PATH = os.path.join(os.path.dirname(__file__), "evidence.json")
SUPPLIER = "Suministros Altiplano Cauca SAS"

evidence: dict = {"criteria": {}}


def log(msg: str) -> None:
    print(f"[verify] {msg}", flush=True)


def publish(event: dict) -> str:
    publisher = pubsub_v1.PublisherClient()
    future = publisher.publish(
        publisher.topic_path(PROJECT, TOPIC), json.dumps(event).encode()
    )
    return future.result(timeout=60)


def wait_for_command(db, case_id: str, timeout: int = 180) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        command = get_command(db, case_id, "create_supplier", 1)
        if command and command.get("status") == DONE:
            return command
        time.sleep(5)
    return get_command(db, case_id, "create_supplier", 1)


def ingress_url() -> str:
    return subprocess.run(
        [
            "gcloud", "run", "services", "describe", "keplaria-ingress",
            "--region=us-central1", "--format=value(status.url)",
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def anonymous_status(method: str, url: str, body: str | None = None) -> str:
    """HTTP status an unauthenticated caller gets back — no gcloud identity token."""
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", method, url]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", body]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    case_id = f"TV-{uuid.uuid4().hex[:10].upper()}"
    event = {
        "event_id": f"evt-{uuid.uuid4().hex[:10]}",
        "case_id": case_id,
        "event_type": "new_supplier_packet",
        "department": "procurement",
        "supplier": SUPPLIER,
        "schema_version": 1,
    }
    evidence["case_id"] = case_id
    evidence["event_id"] = event["event_id"]

    # --- the endpoint must refuse anonymous callers, proven with two witnesses ---
    url = ingress_url()
    push_status = anonymous_status(
        "POST", f"{url}/pubsub/push", body=json.dumps({"message": {"data": ""}})
    )
    root_status = anonymous_status("GET", f"{url}/")
    evidence["unauthenticated_status"] = {
        "post_pubsub_push": push_status,
        "get_root": root_status,
    }
    evidence["criteria"]["ingress_requires_authentication"] = (
        push_status == "403" and root_status == "403"
    )
    log(f"anonymous POST /pubsub/push -> {push_status}, anonymous GET / -> {root_status}")

    # --- first delivery ----------------------------------------------------
    evidence["published_message_id"] = publish(event)
    log(f"published {event['event_id']} as message {evidence['published_message_id']}")

    db = get_client()
    command = wait_for_command(db, case_id)
    evidence["command"] = json.loads(json.dumps(command, default=str))
    executed = bool(command) and command.get("status") == DONE
    evidence["criteria"]["erp_supplier_created"] = executed
    log(f"command status: {command.get('status') if command else 'MISSING'}")

    case_ref = db.collection("cases").document(case_id)
    case = case_ref.get().to_dict() or {}
    evidence["case"] = json.loads(json.dumps(case, default=str))
    evidence["routing"] = evidence["case"].get("routing")
    evidence["screening"] = evidence["case"].get("screening")
    evidence["criteria"]["case_at_version_1"] = case.get("case_version") == 1
    evidence["criteria"]["routing_decision_persisted"] = bool(
        evidence["routing"] and evidence["routing"].get("route")
    )
    evidence["criteria"]["screening_summary_persisted"] = bool(
        evidence["screening"] and "reachable" in evidence["screening"]
    )
    log(f"routing persisted: {evidence['routing']}")
    log(f"screening persisted: {evidence['screening']}")

    # --- replay the identical event ---------------------------------------
    replay_id = publish(event)
    log(f"republished the SAME event as message {replay_id}")
    time.sleep(30)

    replayed = get_command(db, case_id, "create_supplier", 1)
    case_after = case_ref.get().to_dict() or {}
    evidence["duplicate_replay"] = {
        "message_id": replay_id,
        "attempts": (replayed or {}).get("attempts"),
        "case_version_after": case_after.get("case_version"),
        "note": (
            "attempts counts graph claim_command acquisitions, not ERP call"
            " attempts (the executor does not increment it); together with"
            " case_version this proves the duplicate was not reprocessed,"
            " while the absent second ERP write rests on the executor's"
            " DONE-skip and the ERP's deterministic-ID uniqueness"
        ),
    }
    not_reprocessed = (replayed or {}).get("attempts") == 1 and case_after.get(
        "case_version"
    ) == 1
    evidence["criteria"]["duplicate_not_reprocessed"] = not_reprocessed
    log(f"after replay: attempts={(replayed or {}).get('attempts')} "
        f"case_version={case_after.get('case_version')}")

    evidence["passed"] = all(evidence["criteria"].values())
    with open(EVIDENCE_PATH, "w") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
    log(f"evidence written to {EVIDENCE_PATH}")

    for name, ok in evidence["criteria"].items():
        log(f"  {'PASS' if ok else 'FAIL'}  {name}")
    log("PASS" if evidence["passed"] else "FAIL")
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
