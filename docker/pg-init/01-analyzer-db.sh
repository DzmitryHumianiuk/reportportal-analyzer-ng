#!/bin/bash
# Example PostgreSQL init script for the "hardened" deployment path (spec 01 §7.2).
#
# analyzer-ng normally creates its own database at startup (ANALYZER_PG_CREATE_DB=true),
# because the RP compose runs postgres as a superuser. When that is disabled
# (ANALYZER_PG_CREATE_DB=false) the operator must pre-create the database. Mount this
# script into the postgres container so it runs once on first cluster init:
#
#   services:
#     postgres:
#       volumes:
#         - ./docker/pg-init/01-analyzer-db.sh:/docker-entrypoint-initdb.d/01-analyzer-db.sh:ro
#
# It runs as the POSTGRES_USER superuser against the RP maintenance database and
# creates a SEPARATE database (default "analyzer"), enabling pgvector + the schema.
# The "reportportal" database is never touched. Idempotent — safe to leave mounted.
set -euo pipefail

ANALYZER_DB="${ANALYZER_PG_DB:-analyzer}"
ANALYZER_SCHEMA="${ANALYZER_PG_SCHEMA:-analyzer}"

# CREATE DATABASE cannot run inside a transaction block, so use psql's \gexec to
# conditionally emit + execute it only when the database is absent.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	SELECT format('CREATE DATABASE %I', '${ANALYZER_DB}')
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${ANALYZER_DB}')\gexec
EOSQL

# Enable the vector extension and create the schema inside the analyzer database
# (the service also does this at startup; doing it here supports create-db=false).
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$ANALYZER_DB" <<-EOSQL
	CREATE EXTENSION IF NOT EXISTS vector;
	CREATE SCHEMA IF NOT EXISTS ${ANALYZER_SCHEMA};
EOSQL

echo "[01-analyzer-db] ensured database '${ANALYZER_DB}' (schema '${ANALYZER_SCHEMA}', pgvector enabled)"
