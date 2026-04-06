#!/bin/bash
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name')

if [ "$TOOL_NAME" = "Write" ] || [ "$TOOL_NAME" = "Edit" ]; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.filePath // ""')
  # Only lint Python files in src/ or tests/
  if echo "$FILE_PATH" | grep -qE '\.(py)$'; then
    if [ -f "$FILE_PATH" ]; then
      RESULT=$(cd "$CLAUDE_PROJECT_DIR" && source .venv/bin/activate && ruff check "$FILE_PATH" 2>&1)
      if [ -n "$RESULT" ]; then
        echo "### Ruff Lint Results for $FILE_PATH"
        echo "$RESULT"
        echo ""
      fi
    fi
  fi
fi
exit 0
