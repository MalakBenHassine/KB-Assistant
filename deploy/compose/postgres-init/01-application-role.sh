#!/bin/sh
# Runs once, when the Postgres volume is first initialised.
#
# Two roles, two jobs:
# - POSTGRES_USER owns the schema and runs migrations;
# - kb_app is what the API and the worker connect as. It does not own the
#   tables, so row-level security applies to it, and it cannot bypass RLS.
set -eu

# The password goes through a psql variable (:'app_password'), which psql
# quotes, so a password containing a quote cannot break out of the literal.
psql -v ON_ERROR_STOP=1 -v app_password="$KB_APP_DB_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE kb_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO kb_app', current_database()) \gexec
SQL
