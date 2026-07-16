-- Run as a superuser / DBA. Password supplied via psql -v svc_password=...
-- Least-privilege setup (spec 02 §1.3): the service then starts with
-- ANALYZER_PG_CREATE_DB=false and only ever touches schema `analyzer`.
CREATE ROLE analyzer_svc LOGIN PASSWORD :'svc_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE analyzer OWNER analyzer_svc;
\connect analyzer
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- The service owns its schema and nothing else:
CREATE SCHEMA analyzer AUTHORIZATION analyzer_svc;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO analyzer_svc;  -- pgvector types live here
