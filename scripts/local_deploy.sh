#!/bin/bash
# Builds the image and (re)starts gateway, Redis and orchestrator.
set -e
docker compose up -d --build
echo "Open http://localhost and pick a username; you'll land on http://<username>.localhost"
echo "Traefik dashboard: http://localhost:8080 · Logs: docker compose logs -f"
