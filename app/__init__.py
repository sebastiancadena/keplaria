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

"""Package root.

``app`` is imported both by the Agent Runtime container (which needs the full
ADK graph in ``.agent``) and by the Cloud Run ingress adapter (which only
needs ``app.schemas`` / ``app.state.firestore`` and never installs
``google-adk``). A top-level ``from .agent import app`` would force every
importer of any submodule to pull in the graph — exactly the pattern already
avoided in ``app/agent.py`` consumers like ``reasoning_engine_adapter.py`` and
``fast_api_app.py``, which import ``app.agent`` lazily inside functions
instead. ``__getattr__`` (PEP 562) gives plain ``from app import app`` the
same laziness without changing its call sites.
"""

__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        from .agent import app as adk_app

        return adk_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
