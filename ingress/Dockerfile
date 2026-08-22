FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY ingress/pyproject.toml /srv/ingress/pyproject.toml
RUN uv pip install --system --no-cache -r /srv/ingress/pyproject.toml

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
