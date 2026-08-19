FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY console/pyproject.toml /srv/console/pyproject.toml
RUN uv pip install --system --no-cache -r /srv/console/pyproject.toml

# The console shares the case schema, the projection's band logic and the
# executor with the graph.
COPY app /srv/app
COPY console /srv/console

# app/risk.py's DEFAULT_POLICY_PATH resolves to policy/supplier_risk.v2.json,
# which lives outside app/ and so does not arrive with the COPY above. No
# route in either app calls load_policy today — effective_band reads the band
# the graph already wrote onto the case — but app.executor.runner is on the
# review service's import graph, and the failure mode if that ever changes is
# silent: load_policy would raise, assess would return POLICY_UNAVAILABLE, and
# the case would come back `blocked` with nothing on the page to say the
# policy file was simply absent from the image.
COPY policy /srv/policy

ENV PYTHONPATH=/srv
EXPOSE 8080
CMD ["uvicorn", "console.public:api", "--host", "0.0.0.0", "--port", "8080"]
