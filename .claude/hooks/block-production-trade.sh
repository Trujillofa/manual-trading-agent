#!/bin/bash
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name')

if [ "$TOOL_NAME" = "Bash" ]; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')
  
  # Block switching to live mode
  if echo "$COMMAND" | grep -qE 'mode.*live|mode.*real|trading.*mode.*"(live|real)"'; then
    jq -n '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "BLOCKED: Switching to live trading mode requires explicit human approval. Change config/settings.yaml manually."
      }
    }'
    exit 0
  fi
  
  # Warn about docker compose on production
  if echo "$COMMAND" | grep -qE 'docker.*(up|restart).*manual-trading'; then
    echo "⚠️  Docker operation on manual-trading-agent detected. Ensure you're deploying the correct version."
  fi
fi
exit 0
