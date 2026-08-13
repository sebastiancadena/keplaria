"""AGENT_ENGINE_LOCATION and GOOGLE_CLOUD_LOCATION must stay independent.

GOOGLE_CLOUD_LOCATION has to be "global" for gemini-3.6-flash model calls to
resolve, but the Agent Engine REST endpoint has no "global" host. If a future
edit ever merges the two variables, the engine URL silently becomes
"global-aiplatform.googleapis.com", which does not exist. This test is the
guard for that — a code comment alone doesn't fail a build.
"""

from __future__ import annotations

import importlib


class _FakeCredentials:
    token = "fake-token"

    def refresh(self, request):  # noqa: ARG002 - signature match, no network call
        pass


def test_engine_endpoint_uses_agent_engine_location_not_google_cloud_location(
    monkeypatch,
):
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("AGENT_ENGINE_LOCATION", "us-central1")
    monkeypatch.setenv(
        "AGENT_ENGINE_RESOURCE",
        "projects/keplaria/locations/us-central1/reasoningEngines/123",
    )

    import ingress.engine_client as engine_client

    importlib.reload(engine_client)

    monkeypatch.setattr(
        "google.auth.default", lambda **kwargs: (_FakeCredentials(), "keplaria")
    )

    client = engine_client._client()
    try:
        assert client.base_url.host == "us-central1-aiplatform.googleapis.com"
    finally:
        client.close()
        importlib.reload(engine_client)  # restore module state for later tests
