#!/bin/bash
# Create a git worktree for parallel strategy testing
# Usage: ./scripts/worktree-create.sh <branch-name> [base-branch]
# Example: ./scripts/worktree-create.sh rsi-test-21 rsi-period-21

set -euo pipefail

BRANCH_NAME="${1:?Usage: worktree-create.sh <branch-name> [base-branch]}"
BASE_BRANCH="${2:-main}"
WORKTREE_PARENT="../.worktrees/manual-trading-agent"
WORKTREE_DIR="${WORKTREE_PARENT}/${BRANCH_NAME}"

echo "Creating worktree: ${WORKTREE_DIR}"
echo "  Branch: ${BRANCH_NAME}"
echo "  Base: ${BASE_BRANCH}"

# Check if worktree already exists
if [ -d "${WORKTREE_DIR}" ]; then
  echo "ERROR: ${WORKTREE_DIR} already exists"
  exit 1
fi

mkdir -p "${WORKTREE_PARENT}"

# Create worktree with new branch
git worktree add "${WORKTREE_DIR}" -b "${BRANCH_NAME}" "${BASE_BRANCH}"

echo ""
echo "✅ Worktree created at: ${WORKTREE_DIR}"
echo "   cd ${WORKTREE_DIR} to work in it"
echo ""
echo "Run backtest there:"
echo "   cd ${WORKTREE_DIR} && source .venv/bin/activate && python -m src.cli backtest --pair EUR/USD --start 2024-01-01"
