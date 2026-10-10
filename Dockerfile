# syntax=docker/dockerfile:1

# ==========================================
# Stage 1: Frontend Builder (Node.js)
# ==========================================
FROM node:24-bookworm-slim AS frontend-builder

WORKDIR /app

# Copia i file di dipendenza
COPY package*.json ./

# Usa npm install per ora (manca package-lock.json nel repo)
RUN npm install

# Copia i sorgenti e compila il CSS
COPY tailwind.config.js ./
COPY src ./src
RUN mkdir -p src/static/css
RUN npm run build

# ==========================================
# Stage 2: Python Builder (Dependency Compilation)
# ==========================================
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# Variabili ambiente per pip
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dipendenze di sistema per la build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Installazione Poetry e Plugin Export (FIX per errore export)
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"
RUN poetry self add poetry-plugin-export

COPY pyproject.toml poetry.lock README.md ./

# Esportazione requirements.txt standard
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

# --- FIX CRITICO: Creazione Virtualenv ---
# Invece di --target, creiamo un venv completo.
# Questo garantisce che 'bin/uvicorn' venga generato correttamente.
RUN python -m venv /app/venv

# Installazione dipendenze nel venv
RUN /app/venv/bin/pip install -r requirements.txt

# ==========================================
# Stage 3: Final Runtime (Hardened)
# ==========================================
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# Creazione utente non-root
RUN groupadd -r flcuser && useradd -r -g flcuser flcuser

# --- FIX CRITICO: Copia del Virtualenv ---
# Copiamo l'intero ambiente virtuale dal builder
COPY --from=builder /app/venv /app/venv

# Aggiungiamo il bin del venv al PATH di sistema
# Così 'uvicorn' viene trovato immediatamente senza path assoluti
ENV PATH="/app/venv/bin:$PATH"

# Copia codice applicativo e asset statici
COPY src ./src
COPY --from=frontend-builder /app/src/static/css/styles.css /app/src/static/css/styles.css

# Creiamo la cartella /data e assegnamo i permessi a flcuser.
# Quando Docker monterà il volume, erediterà questi permessi.
RUN mkdir -p /data && chown -R flcuser:flcuser /data

# Permessi App
RUN chown -R flcuser:flcuser /app

USER flcuser

# Ottimizzazioni Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Traefik only routes to healthy containers: probe every second while starting, so a
# new node is reachable as soon as it's up. Stdlib probe, no curl in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=1s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/api/health', timeout=2)"]

# PID 1 signal handling and zombie reaping come from `init: true` (compose) / init=True (orchestrator).
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
