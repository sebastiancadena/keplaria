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

# Must match `requires-python` in pyproject.toml. uv.lock is resolved for
# >=3.13; installing it into a 3.12 venv builds cleanly but produces an
# environment that fails on import at container start, which Agent Runtime
# reports only as "failed to start and cannot serve traffic" with no logs.
FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY ./pyproject.toml ./README.md ./uv.lock* ./

COPY ./app ./app

# app/risk.py's DEFAULT_POLICY_PATH resolves to policy/supplier_risk.v1.json
# and app/documents.py's FIXTURE_ROOT resolves to fixtures/documents/ at
# runtime — both outside app/. .gcloudignore controls what reaches the build
# context, but it is this COPY list that decides what actually lands in the
# image; omitting either directory here means load_policy() and
# load_document() both fail closed (DocumentUnavailable -> quarantine,
# POLICY_UNAVAILABLE -> blocked) on every case that reaches them, silently,
# because .gcloudignore alone does not catch it.
COPY ./policy ./policy
COPY ./fixtures ./fixtures

RUN uv sync --frozen

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
