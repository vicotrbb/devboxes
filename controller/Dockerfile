# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.29 AS uv
FROM python:3.14-slim-bookworm

LABEL org.opencontainers.image.title="Devboxes Controller" \
      org.opencontainers.image.description="Controller and dashboard for self-hosted Kubernetes development environments" \
      org.opencontainers.image.source="https://github.com/vicotrbb/devboxes" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin devboxes \
    && chown -R devboxes:devboxes /app

USER 10001
EXPOSE 8000

ENTRYPOINT ["devboxes-controller"]
