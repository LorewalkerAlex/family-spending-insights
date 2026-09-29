# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.12.17
ARG PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

FROM python:3.14-slim-trixie AS api

ARG UV_VERSION
ARG PYPI_INDEX_URL

RUN sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 family-spending \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin family-spending

RUN python -m pip install --no-cache-dir --index-url "${PYPI_INDEX_URL}" "uv==${UV_VERSION}"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY --chown=10001:10001 src /app/src
COPY --chown=10001:10001 deploy/family-spending.docker.toml /app/family-spending.toml

USER 10001:10001

EXPOSE 8765

CMD ["python", "-m", "family_spending", "--config", "/app/family-spending.toml", "serve"]


FROM node:24-alpine AS web-builder

ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
COPY frontend ./frontend
RUN --mount=type=cache,target=/root/.npm \
    npm ci --registry="${NPM_REGISTRY}"
RUN npm run build:web


FROM nginx:1.29-alpine AS web

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web-builder /app/frontend/apps/web/dist /usr/share/nginx/html

EXPOSE 80
