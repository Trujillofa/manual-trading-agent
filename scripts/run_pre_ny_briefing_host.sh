#!/bin/sh
# Run pre-NY briefing on the Hetzner host as emilio.
#
# The scan container has no hermes binary and hermes serve is not up.
# This is the scheduled invoke path: PATH + HERMES_HOME belong to emilio,
# so `hermes chat -q` can reach the existing glm-5.2 / Z.AI install.
# Do not pass --safe-mode (drops Z.AI). Failure must not take down the scan loop.

set -eu

APP_DIR="${APP_DIR:-/home/emilio/manual-trading-agent}"
export HOME="${HOME:-/home/emilio}"
export HERMES_HOME="${HERMES_HOME:-/home/emilio/.hermes}"
export PATH="/home/emilio/.local/bin:/home/emilio/bin:${PATH}"

cd "${APP_DIR}"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PYTHON="${PYTHON:-${APP_DIR}/.venv/bin/python}"
if [ ! -x "${PYTHON}" ]; then
  echo "pre-ny-briefing host: missing python at ${PYTHON}" >&2
  exit 1
fi

exec "${PYTHON}" -u -m src.cli pre-ny-briefing "$@"
