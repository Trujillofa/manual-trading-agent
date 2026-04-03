#!/bin/bash
# Show summary of what was done and run quick checks
echo "### Session Wrap-up Summary"
echo ""
echo "Running diagnostics..."
cd "$CLAUDE_PROJECT_DIR"

# Run ruff check on src/ if venv exists
if [ -d ".venv" ]; then
  source .venv/bin/activate
  echo "```"
  ruff check src/ --quiet 2>&1 || true
  echo "```"
fi
exit 0
