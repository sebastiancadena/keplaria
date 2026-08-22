"""Day-2 HITL resume spike: pause → SIGKILL the server → resume in a new process.

Drives the real scaffolded server (app.fast_api_app) end to end:

  Phase A: boot server, create session, POST /run a case → workflow pauses
           with an `adk_request_input` function call. SIGKILL the server.
  Phase B: boot a FRESH server process, POST /run the matching
           functionResponse → workflow must complete with status=approved.
           Fetch the session to prove events persisted in Agent Platform
           Sessions, not process memory.

Exit 0 with a PASS line only if every criterion holds.
Evidence JSON is written next to this file's invocation (EVIDENCE_PATH env
or ./hitl_spike_evidence.json).
"""

import json
import os
import signal
import subprocess
import sys
import time

import httpx

PORT = int(os.environ.get("SPIKE_PORT", "8123"))
BASE = f"http://127.0.0.1:{PORT}"
APP_NAME = "app"
USER_ID = "spike-user"
CASE = {
    "case_id": "SPIKE-001",
    "event_type": "new_supplier_packet",
    "department": "procurement",
    "supplier": "Fictional Supplies S.A.S. (synthetic fixture)",
    "amount": 4200.0,
}
EVIDENCE_PATH = os.environ.get("EVIDENCE_PATH", "hitl_spike_evidence.json")

evidence: dict = {"criteria": {}}


def log(msg: str) -> None:
    print(f"[spike] {msg}", flush=True)


def start_server(tag: str) -> subprocess.Popen:
    log_dir = os.path.dirname(os.path.abspath(EVIDENCE_PATH))
    logfile = open(os.path.join(log_dir, f"server_{tag}.log"), "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.fast_api_app:app", "--port", str(PORT)],
        stdout=logfile,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 180  # first boot may create the Agent Engine
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server {tag} died on startup; see server_{tag}.log")
        try:
            r = httpx.get(f"{BASE}/list-apps", timeout=5)
            if r.status_code == 200:
                log(f"server {tag} up (pid {proc.pid}); apps: {r.json()}")
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError(f"server {tag} did not become healthy in 180s")


def find_request_input(events: list) -> tuple[str, dict] | None:
    """Return (function_call_id, payload) of a pending adk_request_input."""
    for event in events:
        for part in (event.get("content") or {}).get("parts") or []:
            fc = part.get("functionCall")
            if fc and fc.get("name") == "adk_request_input":
                return fc.get("id"), fc.get("args", {})
    return None


def main() -> int:
    # ---- Phase A: pause, then kill -----------------------------------------
    server_a = start_server("phase_a")
    try:
        sid = (
            httpx.post(
                f"{BASE}/apps/{APP_NAME}/users/{USER_ID}/sessions", json={}, timeout=30
            )
            .raise_for_status()
            .json()["id"]
        )
        log(f"session created: {sid}")

        run_a = httpx.post(
            f"{BASE}/run",
            json={
                "appName": APP_NAME,
                "userId": USER_ID,
                "sessionId": sid,
                "newMessage": {
                    "role": "user",
                    "parts": [{"text": json.dumps(CASE)}],
                },
            },
            timeout=120,
        )
        run_a.raise_for_status()
        pending = find_request_input(run_a.json())
        if not pending:
            log(f"FAIL: no adk_request_input in run response: {run_a.json()}")
            return 1
        interrupt_id, payload = pending
        evidence["criteria"]["paused_with_request_input"] = True
        evidence["session_id"] = sid
        evidence["interrupt_id"] = interrupt_id
        evidence["pause_payload"] = payload
        log(f"workflow PAUSED with adk_request_input id={interrupt_id}")
    finally:
        pid_a = server_a.pid
        os.kill(pid_a, signal.SIGKILL)
        server_a.wait()
    evidence["criteria"]["server_killed_sigkill"] = True
    log(f"server phase_a pid {pid_a} destroyed with SIGKILL")

    # ---- Phase B: fresh process, resume ------------------------------------
    server_b = start_server("phase_b")
    try:
        if server_b.pid == pid_a:
            raise RuntimeError("impossible: same pid")
        run_b = httpx.post(
            f"{BASE}/run",
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
                                "response": {
                                    "result": json.dumps({"decision": "approve"})
                                },
                            }
                        }
                    ],
                },
            },
            timeout=120,
        )
        run_b.raise_for_status()
        events_b = run_b.json()
        final_output = None
        for event in events_b:
            if isinstance(event.get("output"), dict) and "status" in event["output"]:
                final_output = event["output"]
        if not final_output or final_output.get("status") != "approved":
            log(f"FAIL: no approved terminal output; events: {events_b}")
            return 1
        evidence["criteria"]["resumed_in_new_process"] = True
        evidence["final_output"] = final_output
        log(f"workflow RESUMED in pid {server_b.pid} and completed: {final_output}")

        # ---- Persistence proof: events live in Agent Platform Sessions ----
        session = (
            httpx.get(
                f"{BASE}/apps/{APP_NAME}/users/{USER_ID}/sessions/{sid}", timeout=30
            )
            .raise_for_status()
            .json()
        )
        n_events = len(session.get("events", []))
        has_call = has_resp = False
        for event in session.get("events", []):
            for part in (event.get("content") or {}).get("parts") or []:
                if (part.get("functionCall") or {}).get("name") == "adk_request_input":
                    has_call = True
                if (part.get("functionResponse") or {}).get(
                    "name"
                ) == "adk_request_input":
                    has_resp = True
        evidence["criteria"]["events_persisted_across_kill"] = has_call and has_resp
        evidence["session_event_count"] = n_events
        log(
            f"session has {n_events} persisted events; "
            f"request_input call={has_call} response={has_resp}"
        )
    finally:
        server_b.kill()
        server_b.wait()

    ok = all(evidence["criteria"].values())
    evidence["verdict"] = "PASS" if ok else "FAIL"
    with open(EVIDENCE_PATH, "w") as f:
        json.dump(evidence, f, indent=2)
    log(f"evidence written to {EVIDENCE_PATH}")
    log(f"VERDICT: {evidence['verdict']} {evidence['criteria']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
