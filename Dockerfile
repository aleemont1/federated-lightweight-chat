# --- Stage 1: Frontend Builder (Node.js) ---
FROM node:18-alpine AS frontend-builder

WORKDIR /app

# Copy dependency definitions
COPY package.json ./

# Install dependencies (including tailwindcss)
RUN npm install

# Copy configuration and source files
COPY tailwind.config.js ./
COPY src ./src

# Create the output directory explicitly to avoid permission issues
RUN mkdir -p src/static/css

# Build the CSS (Minified for production)
RUN npm run build


# --- Stage 2: Python Builder ---
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml poetry.lock README.md ./

RUN poetry config virtualenvs.in-project true \
    && poetry install --no-root --only main --no-interaction --no-ansi


# --- Stage 3: Final Runtime ---
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r flcuser && useradd -r -g flcuser flcuser

# Copy virtual env from Python builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY src ./src
COPY scripts ./scripts

# --- NEW: Copy compiled CSS from Frontend Builder ---
# This overwrites the src folder with the one containing the compiled CSS
COPY --from=frontend-builder /app/src/static/css/styles.css /app/src/static/css/styles.css

RUN chown -R flcuser:flcuser /app

USER flcuser

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
