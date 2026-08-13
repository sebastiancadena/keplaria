FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY ingress/pyproject.toml /srv/ingress/pyproject.toml
RUN uv pip install --system --no-cache -r /srv/ingress/pyproject.toml

# The adapter shares the canonical schema and the inbox transaction with the graph.
COPY app /srv/app
COPY ingress /srv/ingress

ENV PYTHONPATH=/srv
EXPOSE 8080
CMD ["uvicorn", "ingress.main:api", "--host", "0.0.0.0", "--port", "8080"]
