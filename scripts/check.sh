#!/bin/bash
# scripts/check.sh

# Clear terminal for better visibility
clear
echo "---Triggering Quality Checks---"

# 1. Formatters (Fail fast if they modify files)
echo "---Running Black & Isort---"
poetry run black .
poetry run isort .

# 2. Static Analysis
echo "---Running MyPy---"
poetry run mypy src || exit 1

echo "---Running Flake8---"
poetry run flake8 src tests || exit 1

echo "---Running Bandit---"
poetry run bandit -r src -c pyproject.toml || exit 1

# 3. Tests (Only run if static analysis passes)
echo "---Running Tests---"
poetry run pytest -vv --color=yes --cov=src --cov-report=term-missing --cov-fail-under=80

echo "All checks passed!"

