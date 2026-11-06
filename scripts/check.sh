#!/usr/bin/env sh
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD":/src -w /src python:3.12-slim sh -c \
  "pip install -q -e '.[dev]' >/dev/null 2>&1 && ruff check . && ruff format --check . && pytest -q"
status=$?
rm -rf antara.egg-info .pytest_cache .ruff_cache
find . -name __pycache__ -prune -exec rm -rf {} +
exit $status
