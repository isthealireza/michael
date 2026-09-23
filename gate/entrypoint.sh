#!/bin/sh
# gate/entrypoint.sh
set -eu
: "${GATE_SECRET:?GATE_SECRET must be set}"
: "${HERMES_BASE_URL:?HERMES_BASE_URL must be set}"
: "${HERMES_USERNAME:?HERMES_USERNAME must be set}"
: "${HERMES_PASSWORD:?HERMES_PASSWORD must be set}"
exec uvicorn --factory michael.gate.wsgi:app \
     --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers
