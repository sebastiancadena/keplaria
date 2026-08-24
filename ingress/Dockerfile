FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY ingress/pyproject.toml /srv/ingress/pyproject.toml
# constraints.txt is exported from uv.lock (tests/unit/test_container_packaging.py
# fails when it drifts). Without it a build resolves whatever PyPI has that
# minute: on 2026-08-24 google-cloud-firestore 2.29.0 landed between two builds
# and the new image 500'd with "Invalid database id %28default%29".
COPY constraints.txt /srv/constraints.txt
RUN uv pip install --system --no-cache -c /srv/constraints.txt -r /srv/ingress/pyproject.toml

# The adapter shares the canonical schema and the inbox transaction with the graph.
COPY app /srv/app
COPY ingress /srv/ingress

# Data directories that app/ resolves at runtime, none of which arrive with
# the COPY above. Nothing on the ingress's import graph reads them today —
# app.executor.runner touches neither the catalog nor the policy — so these
# are carried for the same reason the console carries policy/: the import
# graph is one refactor away from needing them, and the failure mode is a
# silent fail-closed outage in a container that builds and starts cleanly.
# They total well under 100K. See tests/unit/test_container_packaging.py.
COPY catalog /srv/catalog
COPY fixtures /srv/fixtures
COPY policy /srv/policy

ENV PYTHONPATH=/srv
EXPOSE 8080
CMD ["uvicorn", "ingress.main:api", "--host", "0.0.0.0", "--port", "8080"]
