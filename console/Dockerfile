FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY console/pyproject.toml /srv/console/pyproject.toml
# constraints.txt is exported from uv.lock (tests/unit/test_container_packaging.py
# fails when it drifts). Without it a build resolves whatever PyPI has that
# minute: on 2026-08-24 google-cloud-firestore 2.29.0 landed between two builds
# and the new image 500'd with "Invalid database id %28default%29".
COPY constraints.txt /srv/constraints.txt
RUN uv pip install --system --no-cache -c /srv/constraints.txt -r /srv/console/pyproject.toml

# The console shares the case schema, the projection's band logic and the
# executor with the graph.
COPY app /srv/app
COPY console /srv/console

# Data directories that app/ resolves at runtime. They live outside app/ and
# so do NOT arrive with the COPY above, and nothing about a missing one is
# visible at build time — the image builds, starts, and serves.
#
# catalog/ is load-bearing for this image specifically: console/public.py's
# /fleet route calls get_catalog(), and shipping without it renders
# fleet_unavailable.html with a 503 on every request. That is exactly what
# happened on 2026-08-22, on the first deploy after the catalog merged.
#
# policy/ has no caller on either app's routes today — effective_band reads
# the band the graph already wrote onto the case — but app.executor.runner is
# on the review service's import graph, and if that ever changes the failure
# is silent: load_policy raises, assess returns POLICY_UNAVAILABLE, and the
# case comes back `blocked` with nothing on the page to say the file was
# simply absent from the image.
#
# tests/unit/test_container_packaging.py discovers this list from the code and
# fails if an image carrying app/ omits any of it. Add to that test, not just
# here, if you find a fourth.
COPY catalog /srv/catalog
COPY fixtures /srv/fixtures
COPY policy /srv/policy

ENV PYTHONPATH=/srv
EXPOSE 8080
CMD ["uvicorn", "console.public:api", "--host", "0.0.0.0", "--port", "8080"]
