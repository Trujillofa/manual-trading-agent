#!/bin/bash
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.file_path // .changed_file // ""')

# Alert when settings.yaml changes
if echo "$FILE_PATH" | grep -q 'settings.yaml'; then
  echo "⚠️  settings.yaml was modified. Key config changes may require restarting the scanner."
  echo "Current TP/SL:"
  grep -E 'tp_usd:|sl_usd:' "$CLAUDE_PROJECT_DIR/config/settings.yaml" 2>/dev/null || true
fi

# Alert when .env changes
if echo "$FILE_PATH" | grep -q '\.env$'; then
  echo "⚠️  .env was modified. API credentials may have changed."
fi
exit 0
