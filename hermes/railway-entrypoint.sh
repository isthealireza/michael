#!/bin/sh
# Container entrypoint for Railway.
#
# Renders /opt/data/config.yaml and /opt/data/SOUL.md from the environment,
# then hands over to the image's own entrypoint. /init is s6-overlay's PID 1
# and must stay first in the chain, or the supervision tree never starts and
# the gateway does not run.
set -eu

# Railway assigns the public port. The dashboard must listen on it, not on the
# hardcoded 9119, or the HTTPS domain reaches nothing.
if [ -n "${PORT:-}" ]; then
    export HERMES_DASHBOARD_PORT="$PORT"
fi
export HERMES_DASHBOARD_HOST="${HERMES_DASHBOARD_HOST:-0.0.0.0}"
export HERMES_DASHBOARD="${HERMES_DASHBOARD:-1}"

/opt/michael/.venv/bin/python /opt/michael/hermes/render_runtime_config.py

# The supervision tree drops to uid 10000; it must own what was just written.
chown -R 10000:10000 /opt/data 2>/dev/null || true

exec /init /opt/hermes/docker/main-wrapper.sh "$@"
