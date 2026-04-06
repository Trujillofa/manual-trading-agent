#!/bin/bash
# Clean up git worktrees
# Usage: ./scripts/worktree-cleanup.sh [branch-name]
# If no branch specified, shows interactive list

set -euo pipefail

if [ -n "${1:-}" ]; then
  BRANCH_NAME="$1"
  WORKTREE_DIR="../manual-trading-agent-${BRANCH_NAME}"

  echo "Removing worktree: ${WORKTREE_DIR}"
  git worktree remove "${WORKTREE_DIR}" --force 2>/dev/null || true
  git branch -d "${BRANCH_NAME}" 2>/dev/null || echo "Branch not deleted (may have unmerged changes)"
  echo "✅ Cleaned up ${BRANCH_NAME}"
else
  echo "Active worktrees:"
  git worktree list
  echo ""
  echo "Usage: worktree-cleanup.sh <branch-name>"
fi
