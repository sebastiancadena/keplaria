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

import json
import os
import uuid

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
from app.state.firestore import CASES, get_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("FRAPPE_API_KEY"),
        reason="FRAPPE_* credentials not in the environment",
    ),
]


def test_agent_stream() -> None:
    """
    Integration test for the agent stream functionality.

    The scaffold version of this test sent free text ("Why is the sky blue?")
    and asserted a text reply came back. That described a generic chat agent;
    this workflow now validates every input against CanonicalEvent and fails
    closed on anything else (see app.nodes.parse_case), so free text is no
    longer a meaningful input here. Adapted to drive the real graph with a
    valid event and assert the terminal structured output streams back over
    the raw ADK Runner, exercising RunConfig(streaming_mode=SSE) directly
    (test_server_e2e.py covers the same graph through the HTTP layer instead).
    """

    session_service = InMemorySessionService()

    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    case_id = f"TEST-{uuid.uuid4().hex[:12]}"
    # certificate_received never populates `screening` (see app/policy.py's
    # allowed_routes()), so assess_risk carries the case's stored verdict
    # forward instead of scoring fresh from screening=None — correct
    # (re-scoring fresh would launder a blocked supplier via a mailed-in
    # certificate), but it means a case with no prior verdict at all fails
    # closed to `blocked` rather than reaching commit_commands. This test is
    # about a different property (the graph produces structured output over
    # this transport), so seed a `clear` verdict directly rather than route
    # through a full onboarding sequence first.
    get_client().collection(CASES).document(case_id).set({
        "case_id": case_id,
        "policy": {"policy_id": "supplier_risk", "policy_version": 1, "score": 0.0,
                   "band": "clear", "factors_fired": [], "reasons": []},
    })
    event = {
        "event_id": f"evt-{uuid.uuid4().hex[:8]}",
        "case_id": case_id,
        # certificate_received skips compliance screening, so this test does
        # not depend on reaching the private-VPC yente service.
        "event_type": "certificate_received",
        "supplier": "Comercializadora Andes Verde SAS",
        "schema_version": 1,
    }
    message = types.Content(
        role="user", parts=[types.Part.from_text(text=json.dumps(event))]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events) > 0, "Expected at least one message"

    outputs = [ev.output for ev in events if ev.output is not None]
    assert outputs, "Expected the graph to produce at least one structured output"
    # The graph only ever queues the create_supplier command now — see
    # app.nodes.commit_commands — since the Agent Runtime engine has no public
    # internet path to Frappe Cloud. Execution happens outside the graph.
    assert outputs[-1]["status"] == "no_action"
    assert outputs[-1]["case_id"] == case_id
