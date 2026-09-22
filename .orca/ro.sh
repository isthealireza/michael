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

# Pinned, not inherited from whatever the CLI happens to be linked to. This
# machine has two Railway accounts, and only one of them holds michael-hermes:
# the other carries a same-named project whose single deployment failed in
# September and never ran. `railway ssh` defaults to the linked project, so an
# unpinned call is one `railway link` away from reading a different database -
# or from failing in a way that reads as "production is down" rather than "you
# are pointed somewhere else". The service name alone does not disambiguate.
PROJECT=693389ce-128e-469f-ab3b-81901cdc4d8a
SERVICE=michael-hermes
ENVIRONMENT=production
PY=/opt/michael/.venv/bin/python
CLI=/opt/michael/.venv/bin/michael
HERMES=/opt/hermes/.venv/bin/hermes
LOCAL_PYTHON="${LOCAL_PYTHON:-$(command -v python || command -v python3)}"

readonly_env='MICHAEL_DATABASE_URL="$MICHAEL_RO_DATABASE_URL"'

case "${1:-}" in
  python)
    [ -f "${2:-}" ] || { echo "usage: .orca/ro.sh python <script.py>" >&2; exit 2; }
    payload=$($LOCAL_PYTHON -c "
import base64, pathlib, sys
print(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode())" "$2")
    MSYS_NO_PATHCONV=1 railway ssh --project "$PROJECT" --service "$SERVICE" --environment "$ENVIRONMENT" \
      sh -c "$readonly_env $PY -c \"import base64;exec(base64.b64decode('$payload').decode())\"" \
      2>&1 | grep -v "Using SSH key from file"
    ;;
  cli)
    shift
    # Quote each argument for the REMOTE shell. "$*" flattens them, so a
    # multi-word search term arrives as separate arguments and the CLI rejects
    # it - the first thing this helper got wrong.
    remote_args=$($LOCAL_PYTHON -c "
import shlex, sys
print(' '.join(shlex.quote(a) for a in sys.argv[1:]))" "$@")
    MSYS_NO_PATHCONV=1 railway ssh --project "$PROJECT" --service "$SERVICE" --environment "$ENVIRONMENT" \
      sh -c "$readonly_env $CLI $remote_args" 2>&1 | grep -v "Using SSH key from file"
    ;;
  agent)
    shift
    # Ask the DEPLOYED Hermes agent a question. This is the only way to test
    # agent behaviour - the closing blocks, CLASSIFICATION, NOT COVERED and the
    # refusals are the AGENT's, not the operator CLI's, and `cli` above reaches
    # the CLI only.
    #
    # HONEST LIMIT, read it before you rely on it. The other two modes are
    # read-only BY MECHANISM: they repoint both database URLs at michael_ro and
    # Postgres refuses the write. THIS MODE IS NOT. Hermes filters the
    # environment of its stdio MCP subprocess down to an allowlist and supplies
    # MICHAEL_DATABASE_URL from config.yaml, so the override below does not
    # reach the tool process and cannot.
    #
    # What makes this safe is a DIFFERENT guarantee: the answering agent's MCP
    # profile grants exactly three tools - classify_request, search_provisions,
    # draft_document - and excludes apply_schema, ingest_source_url,
    # ingest_local_file and seed_corpus, with no --allow-writes on its argv.
    # There is no ingestion tool for the agent to call whatever URL it holds.
    # That profile is guarded by
    # tests/test_runtime_config.py::test_the_agent_profile_grants_no_write_tool
    # and has been verified in the running container.
    #
    # So: tool-surface read-only, not database-role read-only. Weaker, and the
    # real guarantee for this path. Do not describe it as the same thing.
    #
    # THIS COSTS MONEY. Each run is about $0.01171 of real spend against the
    # owner's OpenRouter key. Do not loop it and do not re-run for polish.
    remote_args=$($LOCAL_PYTHON -c "
import shlex, sys
print(' '.join(shlex.quote(a) for a in sys.argv[1:]))" "$@")
    # --cli is required for non-TTY Railway SSH sessions. Keep the timeout
    # inside the container: a local timeout only closes SSH and leaves the
    # remote Hermes process running with its MCP children.
    MSYS_NO_PATHCONV=1 railway ssh --project "$PROJECT" --service "$SERVICE" --environment "$ENVIRONMENT" \
      sh -c "$readonly_env timeout 120 $HERMES -z $remote_args --cli" 2>&1 | grep -v "Using SSH key from file"
    ;;
  *)
    echo "usage: .orca/ro.sh {python <script.py> | cli <subcommand> [args] | agent <question>}" >&2
    exit 2
    ;;
esac
