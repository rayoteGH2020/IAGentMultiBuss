# syntax=docker/dockerfile:1.7
# Imagen de producción: la misma imagen sirve para la API (uvicorn) y el
# worker ARQ; el comando lo decide deploy/docker-compose.prod.yml.
# Sin secretos en build: todo llega en runtime vía `infisical run` (Agents.md §2).

# ── CSS: Tailwind standalone (misma versión que bin/tailwindcss.exe en dev) ────
FROM debian:bookworm-slim AS css
ARG TAILWIND_VERSION=v3.4.17
# sha256 de tailwindcss-linux-x64 publicado en sha256sums.txt de la release.
ADD --checksum=sha256:7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4 \
    https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64 \
    /usr/local/bin/tailwindcss
RUN chmod +x /usr/local/bin/tailwindcss
WORKDIR /src
COPY tailwind.config.js ./
COPY app/templates ./app/templates
COPY app/static ./app/static
RUN tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify

# ── Dependencias Python ────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --no-install-project: la app se ejecuta desde /app como código fuente.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ── Runtime ────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime
# libmagic1: python-magic detecta el MIME real de las subidas (media_limits).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY --from=css /src/app/static/css/app.css ./app/static/css/app.css
COPY deploy/healthcheck.py /usr/local/bin/app-healthcheck.py
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "/usr/local/bin/app-healthcheck.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
