"""Measures whether Model Armor is reachable from inside the deployed engine.

Recorded run: 2026-08-19, engine service version 15. This file is the probe as
it was deployed, kept so the result can be reproduced rather than believed.

It is NOT wired into anything. To re-run it, drop it back into `app/` and call
it from the `lifespan` context manager in `app/fast_api_app.py`, immediately
after the Runner is constructed:

    # THROWAWAY egress probe — delete with app/_ma_probe.py.
    try:
        from app._ma_probe import run as _ma_probe_run

        logger.log_struct(_ma_probe_run(), severity="WARNING")
    except Exception:  # noqa: BLE001 — a probe must never affect startup
        pass

then deploy, and read the result back with:

    gcloud logging read \
      'resource.labels.reasoning_engine_id="2127503872455868416"
       AND "MODEL_ARMOR_EGRESS_PROBE"' --project keplaria --limit 5

Startup is the only place it can run: Agent Runtime exposes just the
reasoning_engine {class_method, input} contract externally, so an added route
would never be reachable from outside the container.

**Revert it afterwards.** Left in place it costs a 6-second connect timeout on
every cold start, against a graph whose whole run budget is 130s.

Classifies the outcome rather than passing or failing it. An HTTP error is a
SUCCESS for this probe's purposes: a 403 or a 404 proves the packet arrived,
which is the entire question. Only a DNS, connect, or timeout failure means
unreachable. Collapsing those two into "it didn't work" produces a confident
wrong answer — and in the recorded run it would have, because the two hosts
probed disagree.

Every path is caught. A probe that kills the container would present as the
log-less "failed to start and cannot serve traffic".
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 6

# Model Armor's data plane is regional. The global hostname is probed too
# because it is the one an earlier note assumed, and knowing how it differs is
# the point: in the recorded run the global host answered 403 (reachable,
# refused) while the regional host timed out.
HOSTS = {
    "regional": "modelarmor.us-central1.rep.googleapis.com",
    "global": "modelarmor.googleapis.com",
}
TEMPLATE = ("projects/keplaria/locations/us-central1/templates/"
            "keplaria-probe-throwaway")


def _token() -> tuple[str | None, str | None]:
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        return credentials.token, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"


def _classify(host: str, token: str | None) -> dict:
    """Reach for the endpoint and say which of three things happened."""
    result: dict = {"host": host}

    try:
        result["resolved_to"] = socket.gethostbyname(host)
    except Exception as exc:  # noqa: BLE001
        result["outcome"] = "UNREACHABLE_DNS"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return result

    url = f"https://{host}/v1/{TEMPLATE}:sanitizeUserPrompt"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps({"userPromptData": {"text": "probe"}}).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.load(response)
        result["outcome"] = "WORKS"
        result["status"] = response.status
        match = (body.get("sanitizationResult") or {}).get("filterMatchState")
        result["filter_match_state"] = match
    except urllib.error.HTTPError as exc:
        # Reached it. The status is diagnostic, not a failure.
        result["outcome"] = f"REACHABLE_HTTP_{exc.code}"
        result["status"] = exc.code
        result["error"] = str(exc.reason)[:200]
    except (TimeoutError, socket.timeout):
        result["outcome"] = "UNREACHABLE_TIMEOUT"
        result["error"] = f"no answer in {TIMEOUT_SECONDS}s"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            result["outcome"] = "UNREACHABLE_TIMEOUT"
        else:
            result["outcome"] = "UNREACHABLE_CONNECT"
        result["error"] = f"{type(reason).__name__}: {str(reason)[:200]}"
    except Exception as exc:  # noqa: BLE001
        result["outcome"] = "PROBE_ERROR"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return result


def run() -> dict:
    """Never raises. Returns a structured, greppable result."""
    try:
        token, token_error = _token()
        payload = {
            "probe": "MODEL_ARMOR_EGRESS_PROBE",
            "token_acquired": bool(token),
            "token_error": token_error,
            "results": [_classify(host, token) for host in HOSTS.values()],
        }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "probe": "MODEL_ARMOR_EGRESS_PROBE",
            "fatal": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
    print("MODEL_ARMOR_EGRESS_PROBE " + json.dumps(payload), flush=True)
    return payload


if __name__ == "__main__":
    run()
