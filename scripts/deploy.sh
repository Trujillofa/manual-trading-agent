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
#   SERVICE_NAME  — primary docker compose service (default: manual-trading-agent)
#   SIDE_SERVICES — additional services built from this repo (default: etr-renderer)
#
# Order of operations (avoids the rsync-then-checkout collision):
#   1. Abort if the remote clone has unexpected tracked drift
#   2. Fetch and checkout the target SHA on the remote (no git reset --hard)
#   3. Stream git archive → staging → rsync, preserving runtime files
#   4. Verify remote HEAD and .deploy-sha before docker build/up
#
# Every service whose image is built from this repo is rebuilt here. Building
# only SERVICE_NAME left etr-renderer running an image older than the code it
# talks to, so renderer-side fixes could merge and deploy without ever reaching
# production (PR #75 shipped a 447-line renderer change that way).

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
DEPLOY_REF="${1:-HEAD}"
SERVICE_NAME="${SERVICE_NAME:-manual-trading-agent}"
SIDE_SERVICES="${SIDE_SERVICES:-etr-renderer}"
ALL_SERVICES="${SERVICE_NAME} ${SIDE_SERVICES}"
SSH_HOST="${SSH_HOST:?SSH_HOST env var is required (e.g. user@1.2.3.4)}"
REMOTE_PATH="${REMOTE_PATH:?REMOTE_PATH env var is required (e.g. /opt/manual-trading-agent)}"

STAGING_PATH="${REMOTE_PATH}/.staging"
HEALTH_RETRIES=10
HEALTH_SLEEP=5

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[deploy] $*"; }
err()  { echo "[deploy] ERROR: $*" >&2; exit 1; }

cleanup_staging() {
  if [[ -n "${SSH_HOST:-}" && -n "${STAGING_PATH:-}" ]]; then
    ssh "${SSH_HOST}" "rm -rf '${STAGING_PATH}'" >/dev/null 2>&1 || true
  fi
}
trap cleanup_staging EXIT

remote() {
  ssh "${SSH_HOST}" "$@"
}

# ── Pre-flight ────────────────────────────────────────────────────────────────
log "Deploying ref '${DEPLOY_REF}' → ${SSH_HOST}:${REMOTE_PATH}"

COMMIT_SHA=$(git rev-parse "${DEPLOY_REF}")
log "Resolved to commit ${COMMIT_SHA}"

# ── 1. Abort on unexpected tracked drift, then align git BEFORE rsync ─────────
log "Checking remote git working tree and aligning to ${COMMIT_SHA} …"
remote bash <<EOF
set -euo pipefail
cd '${REMOTE_PATH}'
if [ ! -d .git ]; then
  echo "[deploy] no .git at remote path — skipping checkout align"
  exit 0
fi

git fetch origin --prune

tracked_drift="\$(git status --porcelain=v1 --untracked-files=no)"
if [ -n "\${tracked_drift}" ]; then
  echo "[deploy] ERROR: unexpected tracked drift on remote; aborting before checkout/rsync:" >&2
  echo "\${tracked_drift}" >&2
  exit 1
fi

if ! git cat-file -e '${COMMIT_SHA}^{commit}' 2>/dev/null; then
  echo "[deploy] ERROR: ${COMMIT_SHA} not in remote git objects after fetch" >&2
  exit 1
fi

# Leftover untracked files from a failed prior rsync can block checkout.
# Remove only untracked paths that the target commit will write. Never touch
# runtime/preserve paths and never git reset --hard.
while IFS= read -r path; do
  [ -z "\${path}" ] && continue
  case "\${path}" in
    .env|.env/*|logs|logs/*|data|data/*|results|results/*|.deploy-sha|.staging|.staging/*|.ops-backups|.ops-backups/*|.venv|.venv/*)
      continue
      ;;
  esac
  if git ls-tree --name-only -r '${COMMIT_SHA}' | grep -Fxq "\${path}"; then
    rm -rf -- "\${path}"
  fi
done < <(git ls-files --others --exclude-standard)

git checkout -B main '${COMMIT_SHA}'
echo "[deploy] remote HEAD now \$(git rev-parse HEAD)"
EOF

# ── 2. Stage the release archive ──────────────────────────────────────────────
log "Creating staging directory ${STAGING_PATH} …"
remote "mkdir -p '${STAGING_PATH}'"

log "Streaming git archive to remote …"
git archive "${DEPLOY_REF}" \
  | remote "tar -xf - -C '${STAGING_PATH}'"

remote "echo '${COMMIT_SHA}' > '${STAGING_PATH}/.deploy-sha'"

# ── 3. Rsync staged release → live path (preserve runtime + metadata) ─────────
log "Syncing staged release to live path …"
remote bash <<EOF
set -euo pipefail
rsync -a --delete \
  --exclude='.env' \
  --exclude='.env/' \
  --exclude='logs' \
  --exclude='logs/' \
  --exclude='data' \
  --exclude='data/' \
  --exclude='results' \
  --exclude='results/' \
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
  --exclude='.deploy-sha' \
  '${STAGING_PATH}/' '${REMOTE_PATH}/'
EOF

remote "echo '${COMMIT_SHA}' > '${REMOTE_PATH}/.deploy-sha'"

# ── 4. Verify HEAD and .deploy-sha before building ────────────────────────────
log "Verifying remote HEAD and .deploy-sha …"
remote bash <<EOF
set -euo pipefail
cd '${REMOTE_PATH}'
deployed="\$(tr -d '[:space:]' < .deploy-sha)"
if [ "\${deployed}" != '${COMMIT_SHA}' ]; then
  echo "[deploy] ERROR: .deploy-sha is \${deployed}, expected ${COMMIT_SHA}" >&2
  exit 1
fi
if [ -d .git ]; then
  head="\$(git rev-parse HEAD)"
  if [ "\${head}" != '${COMMIT_SHA}' ]; then
    echo "[deploy] ERROR: remote HEAD is \${head}, expected ${COMMIT_SHA}" >&2
    exit 1
  fi
fi
echo "[deploy] verified HEAD/SHA ${COMMIT_SHA}"
EOF

# ── 5. Host cron as emilio so scheduled Plan NY can call Hermes ───────────────
# The scan container has no hermes binary and hermes serve is not listening.
# BRIEFING_INVOKE=host (compose default) skips in-container briefing.
log "Installing host pre-NY briefing cron as emilio …"
remote bash <<EOF
set -euo pipefail
# Older SHAs (and deploy-alignment fixtures) do not have the host script.
# Skip rather than fail chmod; production this SHA always has the file.
if [ ! -f '${REMOTE_PATH}/scripts/run_pre_ny_briefing_host.sh' ]; then
  echo "[deploy] host briefing script not in this SHA — skip cron install"
  exit 0
fi
chmod +x '${REMOTE_PATH}/scripts/run_pre_ny_briefing_host.sh'
mkdir -p '${REMOTE_PATH}/logs'
touch '${REMOTE_PATH}/logs/briefing.log' '${REMOTE_PATH}/logs/pre_ny_briefing_state.json'
if id emilio >/dev/null 2>&1; then
  chown emilio:emilio '${REMOTE_PATH}/logs/briefing.log' '${REMOTE_PATH}/logs/pre_ny_briefing_state.json'
fi
chmod 664 '${REMOTE_PATH}/logs/briefing.log' '${REMOTE_PATH}/logs/pre_ny_briefing_state.json'
if [ ! -d /etc/cron.d ] || [ ! -w /etc/cron.d ]; then
  echo "[deploy] ERROR: /etc/cron.d is not writable; host Hermes cron cannot be installed" >&2
  exit 1
fi
cat > /etc/cron.d/manual-trading-pre-ny-briefing <<CRON
SHELL=/bin/sh
PATH=/home/emilio/.local/bin:/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
MAILTO=""
*/15 * * * * emilio APP_DIR=${REMOTE_PATH} /bin/sh ${REMOTE_PATH}/scripts/run_pre_ny_briefing_host.sh >> ${REMOTE_PATH}/logs/briefing.log 2>&1
CRON
chmod 644 /etc/cron.d/manual-trading-pre-ny-briefing
EOF

# ── 6. Build & restart Docker services ────────────────────────────────────────
log "Building Docker images for: ${ALL_SERVICES} …"
remote bash <<EOF
set -euo pipefail
cd '${REMOTE_PATH}'
GIT_SHA='${COMMIT_SHA}' docker compose build ${ALL_SERVICES}
GIT_SHA='${COMMIT_SHA}' docker compose up -d --no-deps ${ALL_SERVICES}
EOF

# ── 7. Health check ───────────────────────────────────────────────────────────
check_health() {
  local service="$1"
  log "Waiting for '${service}' health check …"
  for i in $(seq 1 "${HEALTH_RETRIES}"); do
    local status
    status=$(remote \
      "docker inspect --format='{{.State.Health.Status}}' '${service}' 2>/dev/null || echo 'not-found'")

    case "${status}" in
      healthy)
        log "'${service}' is healthy ✓"
        return 0
        ;;
      starting)
        log "  [${i}/${HEALTH_RETRIES}] '${service}' still starting … (sleeping ${HEALTH_SLEEP}s)"
        sleep "${HEALTH_SLEEP}"
        ;;
      not-found)
        local running
        running=$(remote \
          "docker inspect --format='{{.State.Running}}' '${service}' 2>/dev/null || echo 'false'")
        if [[ "${running}" == "true" ]]; then
          log "'${service}' is running (no HEALTHCHECK defined) ✓"
          return 0
        fi
        err "Container '${service}' not found after deploy"
        ;;
      *)
        err "Container '${service}' health status: ${status}"
        ;;
    esac

    if [[ "${i}" -eq "${HEALTH_RETRIES}" ]]; then
      err "Health check for '${service}' timed out after $((HEALTH_RETRIES * HEALTH_SLEEP))s"
    fi
  done
}

for service in ${ALL_SERVICES}; do
  check_health "${service}"
done

# ── 8. Verify image revisions ────────────────────────────────────────────────
# Health alone cannot tell a rebuilt image from a stale one; the label can.
# This is what caught nothing when #75's renderer half never shipped.
log "Verifying image revision on every service …"
for service in ${ALL_SERVICES}; do
  revision=$(remote \
    "docker inspect --format='{{index .Config.Labels \"org.opencontainers.image.revision\"}}' '${service}'")
  if [[ "${revision}" != "${COMMIT_SHA}" ]]; then
    err "Service '${service}' runs revision '${revision}', expected ${COMMIT_SHA}"
  fi
  log "  '${service}' revision ${revision} ✓"
done

log "Deploy complete — ${ALL_SERVICES} @ ${COMMIT_SHA}"
