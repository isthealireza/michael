#!/usr/bin/env bash
# Read-only access to the production corpus, for Orca workers.
#
# Both database URLs are pointed at the michael_ro role, whose session carries
# default_transaction_read_only = on. Postgres then refuses every write, so a
# worker cannot mutate production through this path even by calling writable()
# or `michael ingest` - the refusal is the database's, not a promise in a brief.
#
#   .orca/ro.sh python <local-script.py>   run a local Python file remotely
#   .orca/ro.sh cli <subcommand> [args]    run the michael CLI remotely
#
# Production mutation is an operator action and is not available here. If you
# need one, escalate to the ORCHESTRATOR. Do not reach for `railway ssh`
# directly to get around this.
set -euo pipefail

SERVICE=michael-hermes
ENVIRONMENT=production
PY=/opt/michael/.venv/bin/python
CLI=/opt/michael/.venv/bin/michael

readonly_env='MICHAEL_DATABASE_URL="$MICHAEL_RO_DATABASE_URL"'

case "${1:-}" in
  python)
    [ -f "${2:-}" ] || { echo "usage: .orca/ro.sh python <script.py>" >&2; exit 2; }
    payload=$(python -c "
import base64, pathlib, sys
print(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode())" "$2")
    MSYS_NO_PATHCONV=1 railway ssh --service "$SERVICE" --environment "$ENVIRONMENT" \
      sh -c "$readonly_env $PY -c \"import base64;exec(base64.b64decode('$payload').decode())\"" \
      2>&1 | grep -v "Using SSH key from file"
    ;;
  cli)
    shift
    # Quote each argument for the REMOTE shell. "$*" flattens them, so a
    # multi-word search term arrives as separate arguments and the CLI rejects
    # it - the first thing this helper got wrong.
    remote_args=$(python -c "
import shlex, sys
print(' '.join(shlex.quote(a) for a in sys.argv[1:]))" "$@")
    MSYS_NO_PATHCONV=1 railway ssh --service "$SERVICE" --environment "$ENVIRONMENT" \
      sh -c "$readonly_env $CLI $remote_args" 2>&1 | grep -v "Using SSH key from file"
    ;;
  *)
    echo "usage: .orca/ro.sh {python <script.py> | cli <subcommand> [args]}" >&2
    exit 2
    ;;
esac
