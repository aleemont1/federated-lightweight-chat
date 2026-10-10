#!/bin/bash
# End-to-end suite against a freshly built stack (real containers, Redis, network cuts).
set -e
docker compose up -d --build
FLC_E2E=1 poetry run pytest tests/test_e2e.py -v "$@"
