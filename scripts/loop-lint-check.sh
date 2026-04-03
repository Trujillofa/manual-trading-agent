#!/bin/bash
# Continuous lint check - run in a loop or via cron
# Usage: ./scripts/loop-lint-check.sh
# Designed for: claude /loop 5m "./scripts/loop-lint-check.sh"

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

source .venv/bin/activate

echo "=== Lint Check $(date) ==="
RUFF_RESULT=$(ruff check src/ tests/ 2>&1) || true
if [ -z "$RUFF_RESULT" ]; then
  echo "✅ All clean"
else
  echo "⚠️  Issues found:"
  echo "$RUFF_RESULT"
fi

echo ""
echo "=== Type Check ==="
MYPY_RESULT=$(mypy src/ --no-error-summary 2>&1) || true
if [ -z "$MYPY_RESULT" ]; then
  echo "✅ Types clean"
else
  echo "⚠️  Type issues:"
  echo "$MYPY_RESULT"
fi
