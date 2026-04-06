#!/bin/bash
# List all git worktrees with their status
set -euo pipefail

echo "=== Git Worktrees for manual-trading-agent ==="
echo ""
git worktree list
echo ""
echo "=== Branch Summary ==="
git branch -v
