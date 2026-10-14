#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Ruff Fixes ==="
uv run ruff check --fix .

echo "=== Lint ==="
uv run ruff check .

echo "=== Check Formatting ==="
uv run ruff format --check .

echo "=== Cognitive Complexity ==="
uv run complexipy .

echo "=== Dead Code Detection ==="
# Scan project code, not installed dependencies. Dataclass fields can be used
# indirectly, so enforce findings vulture identifies with 100% confidence.
uv run vulture src/kiro_meter tests --min-confidence 100

echo "=== Code Quality Tests ==="
tests/test_code_quality.sh

echo "=== Palette Contrast Gate ==="
uv run python tests/check_contrast.py

echo "=== Dependency Vulnerability Audit ==="
# --no-emit-project: the project itself exports as an editable install, which
# has no single file to hash and so cannot satisfy --require-hashes. Auditing
# wants the pinned dependency set anyway, not the project.
uv run pip-audit --require-hashes -r <(uv export --no-dev --no-emit-project)
