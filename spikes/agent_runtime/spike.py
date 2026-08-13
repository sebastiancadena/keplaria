"""Agent Runtime spike: the three binary criteria of the day-2/3 substrate gate.

Drives the Runtime-hosted app through the Agent Engine `/api` HTTP passthrough,
which exposes the container's ADK routes at

    https://{loc}-aiplatform.googleapis.com/reasoningEngines/v1/{resource}/api/...

so the same routes the day-2 local spike used are reachable against the managed
runtime, authenticated with ADC.

  Criterion 1 — PSC-I reaches yente: the screening node inside the Runtime-hosted
                graph returns a screening result from the private VM at
                10.10.0.2:8000, which has no public address.
  Criterion 2 — Sessions + resumability: the workflow pauses on RequestInput,
                resumes from a separate client process against the managed
                runtime, and its events are read back from Agent Platform
                Sessions rather than process memory.
  Criterion 3 — checked separately against Agent Registry (see spike notes).

Exit 0 with a PASS line only if every asserted criterion holds.
"""

import json
import os
import sys

import google.auth
import google.auth.transport.requests
import httpx

LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
APP_NAME = os.environ.get("SPIKE_APP_NAME", "app")
USER_ID = "runtime-spike-user"
EVIDENCE_PATH = os.environ.get("EVIDENCE_PATH", "agent_runtime_spike_evidence.json")

# The sanctioned company in the synthetic fixture — a hit proves the call
# actually reached the indexed yente instance, not merely a reachable port.
CASE = {
    "case_id": "RUNTIME-SPIKE-001",
    "event_type": "new_supplier_packet",
    "supplier": "Comercializadora Andes Verde SAS",
    "amount": 4200.0,
}

evidence: dict = {"criteria": {}}


def log(msg: str) -> None:
    print(f"[spike] {msg}", flush=True)


def resource_name() -> str:
    """Read the deployed engine resource from deployment_metadata.json."""
    with open("deployment_metadata.json") as f:
        meta = json.load(f)
    name = meta.get("remote_agent_runtime_id")
    if not name:
        raise RuntimeError(f"no remote_agent_runtime_id in metadata: {meta}")
    return name


def make_client(resource: str) -> httpx.Client:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    base = f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/{resource}/api"
    log(f"base: {base}")
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=120,
    )


def find_request_input(events: list) -> tuple[str, dict] | None:
    """Return (function_call_id, args) of a pending adk_request_input."""
    for event in events:
        for part in (event.get("content") or {}).get("parts") or []:
            fc = part.get("functionCall")
            if fc and fc.get("name") == "adk_request_input":
                return fc.get("id"), fc.get("args", {})
    return None


def find_screening(events: list) -> dict | None:
    for event in events:
        out = event.get("output")
        if isinstance(out, dict) and "reachable" in out:
            return out
    return None


def main() -> int:
    resource = resource_name()
    evidence["resource"] = resource
    client = make_client(resource)

    # ---- Phase A: run until the workflow pauses ----------------------------
    sid = (
        client.post(f"/apps/{APP_NAME}/users/{USER_ID}/sessions", json={})
        .raise_for_status()
        .json()["id"]
    )
    evidence["session_id"] = sid
    log(f"session created on the managed runtime: {sid}")

    run_a = client.post(
        "/run",
        json={
            "appName": APP_NAME,
            "userId": USER_ID,
            "sessionId": sid,
            "newMessage": {"role": "user", "parts": [{"text": json.dumps(CASE)}]},
        },
    )
    run_a.raise_for_status()
    events_a = run_a.json()

    # ---- Criterion 1: the screening call left the runtime and hit yente ----
    screening = find_screening(events_a)
    evidence["screening"] = screening
    if not screening:
        log(f"FAIL: no screening event in run output: {json.dumps(events_a)[:2000]}")
        evidence["criteria"]["psci_reaches_yente"] = False
    else:
        hit = [c for c in screening.get("candidates", []) if c["id"] == "syn-co-001"]
        ok = bool(screening.get("reachable")) and bool(hit)
        evidence["criteria"]["psci_reaches_yente"] = ok
        if screening.get("reachable"):
            log(
                f"yente REACHED from Agent Runtime: "
                f"{len(screening.get('candidates', []))} candidates, "
                f"flagged={screening.get('flagged')}"
            )
            for c in screening.get("candidates", [])[:4]:
                log(
                    f"    {c['score']:.3f} match={c['match']} "
                    f"{c['id']} {c['caption']} topics={c['topics']}"
                )
        else:
            log(f"yente UNREACHABLE from Agent Runtime: {screening.get('error')}")

    pending = find_request_input(events_a)
    if not pending:
        log("FAIL: workflow did not pause on adk_request_input")
        evidence["criteria"]["paused_with_request_input"] = False
        return finish()
    interrupt_id, payload = pending
    evidence["criteria"]["paused_with_request_input"] = True
    evidence["interrupt_id"] = interrupt_id
    log(f"workflow PAUSED on the managed runtime, id={interrupt_id}")

    # ---- Phase B: resume from a separate client, fresh credentials ---------
    client_b = make_client(resource)
    run_b = client_b.post(
        "/run",
        json={
            "appName": APP_NAME,
            "userId": USER_ID,
            "sessionId": sid,
            "newMessage": {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "id": interrupt_id,
                            "name": "adk_request_input",
                            "response": {"result": json.dumps({"decision": "approve"})},
                        }
                    }
                ],
            },
        },
    )
    run_b.raise_for_status()
    final_output = None
    for event in run_b.json():
        if isinstance(event.get("output"), dict) and "status" in event["output"]:
            final_output = event["output"]
    evidence["final_output"] = final_output
    resumed = bool(final_output) and final_output.get("status") == "approved"
    evidence["criteria"]["resumed_after_pause"] = resumed
    log(f"resume result: {final_output}")

    # ---- Persistence proof: events come back from Agent Platform Sessions --
    session = (
        client_b.get(f"/apps/{APP_NAME}/users/{USER_ID}/sessions/{sid}")
        .raise_for_status()
        .json()
    )
    has_call = has_resp = False
    for event in session.get("events", []):
        for part in (event.get("content") or {}).get("parts") or []:
            if (part.get("functionCall") or {}).get("name") == "adk_request_input":
                has_call = True
            if (part.get("functionResponse") or {}).get("name") == "adk_request_input":
                has_resp = True
    evidence["session_event_count"] = len(session.get("events", []))
    evidence["criteria"]["events_persisted_in_sessions"] = has_call and has_resp
    log(
        f"session has {evidence['session_event_count']} persisted events; "
        f"request_input call={has_call} response={has_resp}"
    )
    return finish()


def finish() -> int:
    ok = all(evidence["criteria"].values())
    evidence["verdict"] = "PASS" if ok else "FAIL"
    with open(EVIDENCE_PATH, "w") as f:
        json.dump(evidence, f, indent=2)
    log(f"evidence written to {EVIDENCE_PATH}")
    log(f"VERDICT: {evidence['verdict']} {evidence['criteria']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
