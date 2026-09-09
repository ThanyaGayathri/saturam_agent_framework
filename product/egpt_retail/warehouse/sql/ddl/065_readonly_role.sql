-- The read-only warehouse role R6's SQL guard sits above (G3).
--
-- `egpt_reader` is the name already agreed in the API contract's own fixtures
-- (api/openapi/paths/configure.yaml, use-cases.yaml: "Warehouse role egpt_reader is
-- read-only."). The guard (egpt.guardrails.sql_guard) is the ceiling -- it parses every
-- statement and refuses a write before it reaches the database. This role is the floor: even
-- a guard bug cannot turn into a write, because the connection it authenticates simply cannot
-- perform one.
--
-- Postgres has no `CREATE ROLE IF NOT EXISTS`, unlike every other object this package
-- creates -- hence the DO block instead of an inline IF NOT EXISTS clause. Re-running this
-- script is still safe: the existence check makes CREATE ROLE run at most once, and the two
-- GRANTs below are themselves idempotent (a repeated GRANT is a no-op, not an error).
--
-- TODO(AKS cutover): the literal password below is the same dev/compose convention as every
-- other role in this repo (`ai:ai` in deploy/compose.yaml and values.yaml) and is checked in
-- on that basis -- but this role is specifically the guard's DB-level safety floor, and
-- unlike the API keys already routed through Key Vault, this DSN bypasses secret injection
-- entirely. deploy/AKS_CUTOVER.md's Key Vault row tracks provisioning a real password before
-- production; do not carry this literal past kind/compose.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'egpt_reader') THEN
        CREATE ROLE egpt_reader LOGIN PASSWORD 'egpt_reader' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

-- Scoped to `silver` only -- deliberately not `landing` (unparsed raw rows) or `quality`
-- (load metadata). Neither is ever in the semantic layer's table allow-list regardless of
-- this role's own permissions, so granting them here would only widen what a guard bug could
-- expose, never anything a correctly-configured guard would let through anyway.
--
-- A future `gold` schema (R5) will need the same two GRANTs added for `gold` once that
-- schema exists; this file predates it.
-- No `FOR ROLE` clause: unqualified, ALTER DEFAULT PRIVILEGES applies to objects the
-- *executing* role creates from now on, which is exactly whatever role warehouse-migrate
-- runs as -- the same role that creates the silver tables themselves. Naming a role
-- explicitly (e.g. the dev/compose `ai` superuser) would silently stop applying the moment
-- a deployment runs migrations as a different one.
GRANT USAGE ON SCHEMA silver TO egpt_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA silver TO egpt_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT SELECT ON TABLES TO egpt_reader;
