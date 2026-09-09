-- The four warehouse schemas (R4, D5; gold added by R5, D6).
--
-- This is the warehouse database, not the control plane: no framework table lives here, and
-- MigrationManager never touches it (entrypoints/migrate.py says so, and tracker #178
-- hardened it against stamping tables it does not migrate). Applied by
-- `egpt-retail warehouse-migrate`, which is idempotent and may be run at any time.
--
--   landing  raw rows as they arrived, unparsed, with ingest metadata
--   silver   the five conformed entities (K4 harvests these; R5 materialises gold from them)
--   quality  what each load measured, and whether it was allowed through
--   gold     KPI values, materialised nightly from silver by the registry's formulas (R5)

CREATE SCHEMA IF NOT EXISTS landing;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS quality;
CREATE SCHEMA IF NOT EXISTS gold;

COMMENT ON SCHEMA landing IS
    'Stage 1: raw NDJSON rows loaded as-is with ingest metadata. Never transformed in place.';
COMMENT ON SCHEMA silver IS
    'Stage 2: typed, deduplicated, conformed entities. One table per landed stream.';
COMMENT ON SCHEMA quality IS
    'What each load measured and whether it passed. Read by the gates, and later by the KPI '
    'health screen.';
COMMENT ON SCHEMA gold IS
    'Stage 3: KPI values computed in the warehouse from the registry''s formulas (D6), one run '
    'a night (D7). Every row carries its source snapshot and computation time. The product '
    'holds definitions and orchestration; the values live here.';
