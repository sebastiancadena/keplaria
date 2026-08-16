FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY console/pyproject.toml /srv/console/pyproject.toml
RUN uv pip install --system --no-cache -r /srv/console/pyproject.toml

# The console shares the case schema, the projection's band logic and the
# executor with the graph.
COPY app /srv/app
COPY console /srv/console

ENV PYTHONPATH=/srv
EXPOSE 8080
CMD ["uvicorn", "console.public:api", "--host", "0.0.0.0", "--port", "8080"]
