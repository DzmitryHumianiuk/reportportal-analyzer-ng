#!/bin/bash
# PostgreSQL init script for analyzer-ng (spec 01 §7.2; corrected in T5.2 against a
# real ReportPortal stack — see docs/G5-WALKTHROUGH.md and docs/INSTALL.md).
#
# WHY a superuser is needed: the stock RP `postgres` image (bitnami) runs its app
# user (POSTGRES_USER, e.g. `rpuser`) as a NON-superuser. That user has CREATEDB
# (so `ANALYZER_PG_CREATE_DB=true` can create the `analyzer` database) but CANNOT
# `CREATE EXTENSION vector` — pgvector is an untrusted extension, so installing it
# requires SUPERUSER. Therefore the pgvector step here runs as the `postgres`
# superuser. Set POSTGRESQL_POSTGRES_PASSWORD on the postgres service so the
# superuser has a known password:
#
#   services:
#     postgres:
#       environment:
#         POSTGRESQL_POSTGRES_PASSWORD: <superuser-pw>
#       volumes:
#         - ./docker/pg-init/01-analyzer-db.sh:/docker-entrypoint-initdb.d/01-analyzer-db.sh:ro
#
# Runs once on first cluster init. Creates a SEPARATE database (default "analyzer")
# owned by POSTGRES_USER, with pgvector + pg_trgm + the analyzer schema. The
# "reportportal" database is never touched. Idempotent — safe to leave mounted.
#
# Pair with ANALYZER_PG_CREATE_DB=false (the service then only connects + migrates),
# or leave ANALYZER_PG_CREATE_DB=true (the CREATE ... IF NOT EXISTS calls no-op).
set -euo pipefail

ANALYZER_DB="${ANALYZER_PG_DB:-analyzer}"
ANALYZER_SCHEMA="${ANALYZER_PG_SCHEMA:-analyzer}"
APP_USER="${POSTGRES_USER:-rpuser}"
SUPER_USER="${POSTGRESQL_POSTGRES_USER:-postgres}"
export PGPASSWORD="${POSTGRESQL_POSTGRES_PASSWORD:?set POSTGRESQL_POSTGRES_PASSWORD on the postgres service: superuser needed for CREATE EXTENSION vector}"

# CREATE DATABASE cannot run inside a transaction block, so use psql's \gexec to
# conditionally emit + execute it only when the database is absent. Owned by the
# app user so the service (connecting as that user) can manage its own schema.
psql -v ON_ERROR_STOP=1 --username "$SUPER_USER" --dbname postgres <<-EOSQL
	SELECT format('CREATE DATABASE %I OWNER %I', '${ANALYZER_DB}', '${APP_USER}')
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${ANALYZER_DB}')\gexec
EOSQL

# Enable pgvector (superuser-only) + pg_trgm and create the schema inside the
# analyzer database. Idempotent — CREATE EXTENSION IF NOT EXISTS is a no-op once
# the extension exists, so the service (connecting as the app user) finds it ready.
psql -v ON_ERROR_STOP=1 --username "$SUPER_USER" --dbname "$ANALYZER_DB" <<-EOSQL
	CREATE EXTENSION IF NOT EXISTS vector;
	CREATE EXTENSION IF NOT EXISTS pg_trgm;
	CREATE SCHEMA IF NOT EXISTS ${ANALYZER_SCHEMA} AUTHORIZATION ${APP_USER};
EOSQL

echo "[01-analyzer-db] ensured database '${ANALYZER_DB}' (owner '${APP_USER}', schema '${ANALYZER_SCHEMA}', pgvector+pg_trgm enabled)"
