#!/bin/bash
# Fork a Claude Code session — creates a new session with the same context
# Usage: ./scripts/session-fork.sh [session-id]
# If no session-id provided, shows recent sessions to pick from

set -euo pipealfail

SESSION_ID="${1:-}"

if [ -z "$SESSION_ID" ]; then
  echo "Recent Claude Code sessions:"
  echo ""
  # List recent sessions
  RECENT=$(ls -lt ~/.claude/sessions/ 2>/dev/null | head -10)
  if [ -z "$RECENT" ]; then
    echo "No sessions found in ~/.claude/sessions/"
    echo ""
    echo "Usage: session-fork.sh <session-id>"
    echo "  Find session IDs with: ls -lt ~/.claude/sessions/"
    exit 0
  fi
  echo "$RECENT"
  echo ""
  echo "Usage: session-fork.sh <session-id>"
  echo "Example: session-fork.sh abc123-def456"
  exit 0
fi

echo "Forking session: $SESSION_ID"
echo "Starting new Claude Code session with preserved context..."
echo ""

claude --resume "$SESSION_ID" --fork-session
