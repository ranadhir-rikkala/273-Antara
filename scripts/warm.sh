#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
docker run --rm --network host -v "$PWD":/src -w /src python:3.12-slim \
  sh -c 'pip install -q -e . >/dev/null 2>&1 && python scripts/warm.py "$@"' warm "$@"
