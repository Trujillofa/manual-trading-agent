#!/usr/bin/env bash
# deploy.sh — Deploy manual-trading-agent to Hetzner server
#
# Usage: ./scripts/deploy.sh [git-ref]
#   git-ref defaults to HEAD
#
# Required env vars:
#   SSH_HOST     — remote server (e.g. user@1.2.3.4)
#   REMOTE_PATH  — absolute path on server (e.g. /opt/manual-trading-agent)
#
# Optional env vars:
#   SERVICE_NAME — docker compose service name (default: manual-trading-agent)

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
DEPLOY_REF="${1:-HEAD}"
SERVICE_NAME="${SERVICE_NAME:-manual-trading-agent}"
SSH_HOST="${SSH_HOST:?SSH_HOST env var is required (e.g. user@1.2.3.4)}"
REMOTE_PATH="${REMOTE_PATH:?REMOTE_PATH env var is required (e.g. /opt/manual-trading-agent)}"

STAGING_PATH="${REMOTE_PATH}/.staging"
HEALTH_RETRIES=10
HEALTH_SLEEP=5

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[deploy] $*"; }
err()  { echo "[deploy] ERROR: $*" >&2; exit 1; }

# ── Pre-flight ────────────────────────────────────────────────────────────────
log "Deploying ref '${DEPLOY_REF}' → ${SSH_HOST}:${REMOTE_PATH}"

# Resolve the ref to a commit SHA for traceability
COMMIT_SHA=$(git rev-parse "${DEPLOY_REF}")
log "Resolved to commit ${COMMIT_SHA}"

# ── 1. Create staging directory on remote ─────────────────────────────────────
log "Creating staging directory ${STAGING_PATH} …"
ssh "${SSH_HOST}" "mkdir -p '${STAGING_PATH}'"

# ── 2. Stream files via git archive | tar ─────────────────────────────────────
log "Streaming git archive to remote …"
git archive "${DEPLOY_REF}" \
  | ssh "${SSH_HOST}" "tar -xf - -C '${STAGING_PATH}'"

# Write a deploy metadata file
ssh "${SSH_HOST}" "echo '${COMMIT_SHA}' > '${STAGING_PATH}/.deploy-sha'"

# ── 3. Rsync staged release → live path ───────────────────────────────────────
log "Syncing staged release to live path …"
ssh "${SSH_HOST}" bash <<EOF
rsync -a --delete \
  --exclude='.env' \
  --exclude='.env/' \
  --exclude='logs' \
  --exclude='logs/' \
  --exclude='data' \
  --exclude='data/' \
  --exclude='.venv' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='.mypy_cache' \
  --exclude='.staging' \
  --exclude='.staging/' \
  --exclude='.git' \
  --exclude='.git/' \
  --exclude='.ops-backups' \
  --exclude='.ops-backups/' \
  '${STAGING_PATH}/' '${REMOTE_PATH}/'
EOF

# ── 4. Build & restart Docker service ─────────────────────────────────────────
log "Building Docker image for ${SERVICE_NAME} …"
ssh "${SSH_HOST}" bash <<EOF
set -euo pipefail
cd '${REMOTE_PATH}'
docker compose build ${SERVICE_NAME}
docker compose up -d --no-deps ${SERVICE_NAME}
EOF

# ── 5. Health check ───────────────────────────────────────────────────────────
log "Waiting for container health check …"
for i in $(seq 1 "${HEALTH_RETRIES}"); do
  STATUS=$(ssh "${SSH_HOST}" \
    "docker inspect --format='{{.State.Health.Status}}' '${SERVICE_NAME}' 2>/dev/null || echo 'not-found'")

  case "${STATUS}" in
    healthy)
      log "Container is healthy ✓"
      break
      ;;
    starting)
      log "  [${i}/${HEALTH_RETRIES}] Still starting … (sleeping ${HEALTH_SLEEP}s)"
      sleep "${HEALTH_SLEEP}"
      ;;
    not-found)
      # Container may not define a HEALTHCHECK — treat running state as success
      RUNNING=$(ssh "${SSH_HOST}" \
        "docker inspect --format='{{.State.Running}}' '${SERVICE_NAME}' 2>/dev/null || echo 'false'")
      if [[ "${RUNNING}" == "true" ]]; then
        log "Container is running (no HEALTHCHECK defined) ✓"
        break
      fi
      err "Container '${SERVICE_NAME}' not found after deploy"
      ;;
    *)
      err "Container health status: ${STATUS}"
      ;;
  esac

  if [[ "${i}" -eq "${HEALTH_RETRIES}" ]]; then
    err "Health check timed out after $((HEALTH_RETRIES * HEALTH_SLEEP))s"
  fi
done

# ── 6. Cleanup staging ────────────────────────────────────────────────────────
log "Cleaning up staging directory …"
ssh "${SSH_HOST}" "rm -rf '${STAGING_PATH}'"

log "Deploy complete — ${SERVICE_NAME} @ ${COMMIT_SHA}"
