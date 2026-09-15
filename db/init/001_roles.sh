#!/bin/bash
# Runs once, on first initialisation of the michael_postgres_data volume.
#
# Creates the read-only role used by the answering path. The answering path
# never writes to the database; that is enforced by the database, not by
# convention in application code.
set -euo pipefail

if [ -z "${MICHAEL_RO_PASSWORD:-}" ]; then
    echo "MICHAEL_RO_PASSWORD is not set; refusing to create a passwordless role" >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v ro_password="$MICHAEL_RO_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'michael_ro') THEN
        CREATE ROLE michael_ro LOGIN;
    END IF;
END
$$;

ALTER ROLE michael_ro PASSWORD :'ro_password';

-- Read-only at the role level, so even a bug in the answering path cannot
-- write. Table-level SELECT grants are applied by `michael schema apply`,
-- which runs after the tables exist.
ALTER ROLE michael_ro SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO michael_ro;
REVOKE CREATE ON SCHEMA public FROM michael_ro;
SQL
