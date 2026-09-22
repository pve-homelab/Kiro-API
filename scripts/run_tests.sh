#!/usr/bin/env bash
# Run the full test suite (lint + unit + integration) in a container.
# Mirrors what CI does. Builds a small test image with deps baked in so repeat
# runs are fast; source is bind-mounted so edits need no rebuild.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Build the test image if missing (deps only; fast to rebuild).
if ! docker image inspect kiro-api-test:latest >/dev/null 2>&1; then
  docker build -f "$ROOT/docker/Dockerfile.test" -t kiro-api-test:latest "$ROOT"
fi

docker run --rm -v "$ROOT:/proj" -w /proj kiro-api-test:latest sh -c '
  set -e
  chmod +x scripts/kiro-cli-stub.sh
  echo "=== ruff ==="
  ruff check app tray tests
  echo "=== pytest ==="
  pytest
'
