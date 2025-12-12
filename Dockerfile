# --- Stage 1: Builder ---
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies required for building Python packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Copy only dependency files to cache them
COPY pyproject.toml poetry.lock README.md ./

# Configure poetry to create venv in the project folder and install prod deps
RUN poetry config virtualenvs.in-project true \
    && poetry install --no-root --only main --no-interaction --no-ansi

# --- Stage 2: Final Runtime ---
FROM python:3.11-slim AS runtime

WORKDIR /app

# 1. Install Runtime Dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2. Create a non-root user for security
RUN groupadd -r flcuser && useradd -r -g flcuser flcuser

# 3. Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# 4. Copy application code
COPY src ./src
# Copy scripts if needed
COPY scripts ./scripts

# 5. Set ownership to non-root user
RUN chown -R flcuser:flcuser /app

# 6. Switch to non-root user
USER flcuser

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Environment variables optimization
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Expose port (documentary)
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

# Entrypoint
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
