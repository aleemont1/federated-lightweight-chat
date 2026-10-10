# syntax=docker/dockerfile:1
# One image for both the user nodes (default CMD) and the orchestrator (compose overrides CMD).

# --- Stage 1: Tailwind CSS ---
FROM node:24-bookworm-slim AS frontend-builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tailwind.config.js ./
COPY src ./src
RUN npm run build

# --- Stage 2: Python dependencies in a venv ---
FROM python:3.12-slim-bookworm AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install poetry poetry-plugin-export
COPY pyproject.toml poetry.lock README.md ./
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes \
    && python -m venv /app/venv \
    && /app/venv/bin/pip install -r requirements.txt

# --- Stage 3: runtime, non-root ---
FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd -r flcuser && useradd -r -g flcuser flcuser \
    && mkdir -p /data && chown flcuser:flcuser /data
COPY --from=builder /app/venv /app/venv
COPY src ./src
COPY --from=frontend-builder /app/src/static/css/styles.css ./src/static/css/styles.css
USER flcuser
EXPOSE 8000

# Traefik only routes to healthy containers: probe every second while starting, so a
# new node is reachable as soon as it's up. Stdlib probe, no curl in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=1s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/api/health', timeout=2)"]

# PID 1 signal handling and zombie reaping come from `init: true` (compose) / init=True (orchestrator).
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
