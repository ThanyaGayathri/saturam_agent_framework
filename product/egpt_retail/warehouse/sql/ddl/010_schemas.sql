-- The three warehouse schemas (R4, D5).
--
-- This is the warehouse database, not the control plane: no framework table lives here, and
-- MigrationManager never touches it (entrypoints/migrate.py says so, and tracker #178
-- hardened it against stamping tables it does not migrate). Applied by
-- `egpt-retail warehouse-migrate`, which is idempotent and may be run at any time.
--
--   landing  raw rows as they arrived, unparsed, with ingest metadata
--   silver   the five conformed entities (K4 harvests these; R5 materialises gold from them)
--   quality  what each load measured, and whether it was allowed through

CREATE SCHEMA IF NOT EXISTS landing;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS quality;

COMMENT ON SCHEMA landing IS
    'Stage 1: raw NDJSON rows loaded as-is with ingest metadata. Never transformed in place.';
COMMENT ON SCHEMA silver IS
    'Stage 2: typed, deduplicated, conformed entities. One table per landed stream.';
COMMENT ON SCHEMA quality IS
    'What each load measured and whether it passed. Read by the gates, and later by the KPI '
    'health screen.';
