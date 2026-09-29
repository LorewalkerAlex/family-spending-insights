# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.12.19

FROM ghcr.io/astral-sh/uv:${UV_VERSION}-python3.14-trixie-slim AS api-builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


FROM python:3.14-slim-trixie AS api

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 family-spending \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin family-spending

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

COPY --from=api-builder --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 src /app/src
COPY --chown=10001:10001 deploy/family-spending.docker.toml /app/family-spending.toml

USER 10001:10001

EXPOSE 8765

CMD ["python", "-m", "family_spending", "--config", "/app/family-spending.toml", "serve"]


FROM node:24-alpine AS web-builder

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
COPY frontend ./frontend
RUN --mount=type=cache,target=/root/.npm npm ci
RUN npm run build:web


FROM nginx:1.29-alpine AS web

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web-builder /app/frontend/apps/web/dist /usr/share/nginx/html

EXPOSE 80

