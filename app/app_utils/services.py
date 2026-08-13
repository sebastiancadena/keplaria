# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Process-wide ADK session/artifact services shared by every serving surface.

Registered under ``shared://`` so the ADK web routes, the A2A path, and the
reasoning_engine adapter share one instance: a session created on any surface
is visible to the others.
"""

from __future__ import annotations

import functools
import os

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.service_registry import get_service_registry
from google.adk.cli.utils.service_factory import create_session_service_from_options
from vertexai import agent_engines

SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"

_AGENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@functools.cache
def get_session_service():
    """Process-wide session service shared across every serving surface."""
    use_in_memory_session = os.environ.get("USE_IN_MEMORY_SESSION", "").lower() in (
        "true",
        "1",
        "yes",
    )
    if not use_in_memory_session:
        # On Agent Runtime the platform injects the engine ID: bind straight to
        # it. The list/create branch below must NOT run there — the container IS
        # the agent engine, so calling agent_engines.list()/create() while it is
        # still booting blocks startup and the deploy fails with "failed to
        # start and cannot serve traffic", before any logging exists to say so.
        if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
            from google.adk.sessions.vertex_ai_session_service import (
                VertexAiSessionService,
            )

            return VertexAiSessionService(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                # Runtime-injected agent-engine region, not GOOGLE_CLOUD_LOCATION.
                location=os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
                or os.environ.get("GOOGLE_CLOUD_LOCATION"),
                agent_engine_id=agent_engine_id,
            )

        # Cloud Run (and local) path: find or create the engine that backs
        # Agent Platform Sessions.
        default_agent_name = "keplaria"
        agent_name = os.environ.get("AGENT_ENGINE_SESSION_NAME", default_agent_name)
        existing_agents = list(agent_engines.list(filter=f"display_name={agent_name}"))
        engine = (
            existing_agents[0]
            if existing_agents
            else agent_engines.create(display_name=agent_name)
        )
        return create_session_service_from_options(
            base_dir=_AGENT_DIR,
            session_service_uri=f"agentengine://{engine.resource_name}",
        )

    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    return InMemorySessionService()


@functools.cache
def get_artifact_service():
    """Process-wide artifact service: GCS when a bucket is set, else in-memory."""
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


_registry = get_service_registry()
_registry.register_session_service("shared", lambda uri, **kw: get_session_service())
_registry.register_artifact_service("shared", lambda uri, **kw: get_artifact_service())
