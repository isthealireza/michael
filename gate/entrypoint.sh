#!/bin/sh
# gate/entrypoint.sh
set -eu
: "${GATE_SECRET:?GATE_SECRET must be set}"
: "${HERMES_BASE_URL:?HERMES_BASE_URL must be set}"
: "${HERMES_USERNAME:?HERMES_USERNAME must be set}"
: "${HERMES_PASSWORD:?HERMES_PASSWORD must be set}"
# I8: required by michael.config.settings() (michael.gate.users / sessions
# both go through michael.db.writable(), which reads it), but it is neither
# in the runbook's "four variables" nor guarded here previously. Without
# this, the container starts cleanly, serves /login, and 500s on the first
# real login attempt.
: "${MICHAEL_DATABASE_URL:?MICHAEL_DATABASE_URL must be set}"
# No --proxy-headers / --forwarded-allow-ips here: michael.gate.app.client_address
# derives the caller's address from X-Forwarded-For itself (see its docstring
# for why '--forwarded-allow-ips=*' would be a bypass, not just a footgun), so
# uvicorn's own forwarded-header trust machinery is neither needed nor safe to
# enable here.
exec uvicorn --factory michael.gate.wsgi:app \
     --host 0.0.0.0 --port "${PORT:-8080}"
