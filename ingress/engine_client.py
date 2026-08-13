"""Invokes the Agent Runtime-hosted graph over the managed `/api` passthrough.

The passthrough exposes the container's ADK routes under the reasoning engine
resource, so the adapter speaks the same protocol a local ADK server would.
"""

from __future__ import annotations

import json
import os

import google.auth
import google.auth.transport.requests
import httpx

# AGENT_ENGINE_LOCATION is deliberately NOT GOOGLE_CLOUD_LOCATION. The latter
# must be "global" for gemini-3.6-flash model calls to resolve (it 404s at
# us-central1), but the Agent Engine REST endpoint lives at a regional host
# ("global-aiplatform.googleapis.com" does not exist). Reusing one variable
# for both would break whichever caller reads it second — keep them separate.
LOCATION = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")
APP_NAME = os.environ.get("AGENT_APP_NAME", "app")
ENGINE_RESOURCE = os.environ.get("AGENT_ENGINE_RESOURCE", "")
USER_ID = os.environ.get("AGENT_USER_ID", "keplaria-ingress")


def _client() -> httpx.Client:
    if not ENGINE_RESOURCE:
        raise RuntimeError("AGENT_ENGINE_RESOURCE is not set")
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return httpx.Client(
        base_url=(
            f"https://{LOCATION}-aiplatform.googleapis.com"
            f"/reasoningEngines/v1/{ENGINE_RESOURCE}/api"
        ),
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=300,
    )


def invoke_engine(event: dict) -> dict:
    """Run one case through the graph. Returns the session ID and node outputs."""
    with _client() as client:
        session_id = (
            client.post(f"/apps/{APP_NAME}/users/{USER_ID}/sessions", json={})
            .raise_for_status()
            .json()["id"]
        )
        response = client.post(
            "/run",
            json={
                "appName": APP_NAME,
                "userId": USER_ID,
                "sessionId": session_id,
                "newMessage": {
                    "role": "user",
                    "parts": [{"text": json.dumps(event)}],
                },
            },
        )
        response.raise_for_status()
        outputs = [e["output"] for e in response.json() if e.get("output") is not None]
    return {"session_id": session_id, "outputs": outputs}
