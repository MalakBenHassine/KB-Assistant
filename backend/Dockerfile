# syntax=docker/dockerfile:1.7
# One image, three entry points: the API (default), the worker and migrations.
# Multi-stage: build tools never reach the runtime image, which runs as non-root.

ARG PYTHON_IMAGE=python:3.12-slim-trixie

FROM ghcr.io/astral-sh/uv:0.12.15 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first: this layer is reused until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable
# Fetch the tokenizer's encoding at build time: no process needs the network
# at runtime to count tokens.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN /opt/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/tiktoken /opt/tiktoken
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH=/opt/venv/bin:$PATH \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# X-Forwarded-For is interpreted by the application (KB_TRUSTED_PROXY_HOPS), not by uvicorn.
CMD ["uvicorn", "kb_assistant.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header"]
