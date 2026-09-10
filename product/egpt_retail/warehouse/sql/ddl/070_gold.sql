-- gold -- the KPI values R5 materialises nightly (D6: transformation logic executed as SQL in
-- the warehouse; D7: one run before the working day).
--
-- One physical table for every KPI, not one table per KPI. The registry (semantic.kpi_definition
-- in the control plane) is the list of KPIs and can grow every day; a schema that grew with it
-- would need DDL on the nightly path. Grain is carried as a jsonb `dimension` -- `{}` at grain
-- `overall`, the grain-key values otherwise -- and a per-KPI *view* `gold.kpi_<identifier>`
-- projects those keys back out as columns, which is the shape the API contract queries
-- (`SELECT supplier_id, value FROM gold.kpi_supplier__on_time_rate_30d WHERE as_of = ...`).
-- The views are created by materialisation, idempotently, from the columns the formula returned.
--
-- jsonb as a primary-key column: btree equality on jsonb is semantic, so key order in the
-- object does not matter and `{"a":1,"b":2}` and `{"b":2,"a":1}` are one dimension.
--
-- `value` is nullable and NOT the failure signal. NULL means the formula returned NULL (an
-- empty aggregate); a formula that failed writes no value row at all and a `kpi_run` row that
-- says why. A reader that wants to know whether a KPI is trustworthy reads kpi_run, not value.

CREATE TABLE IF NOT EXISTS gold.kpi_value (
    kpi_identifier   text        NOT NULL,
    -- The business date this run computed for, not the wall-clock time it ran at. A re-run on
    -- the same night overwrites (ON CONFLICT in the materialiser); a re-run the next day is a
    -- new row, so history accumulates by as_of.
    as_of            date        NOT NULL,
    dimension        jsonb       NOT NULL DEFAULT '{}',
    value            numeric,
    -- "Every row stamped with source and computation time" (delivery plan, R5). The
    -- snapshot names the semantic layer the formula was validated against, the tables are
    -- the formula's declared sources, the hash is the formula text itself -- a changed
    -- definition is a different lineage even under the same identifier.
    source_snapshot  text        NOT NULL,
    source_tables    text[]      NOT NULL,
    formula_sha256   text        NOT NULL,
    computed_at      timestamptz NOT NULL,
    -- Joins to egpt_answer_provenance.run_id in the control plane, and to gold.kpi_run here.
    run_id           text        NOT NULL,
    PRIMARY KEY (kpi_identifier, as_of, dimension)
);

CREATE INDEX IF NOT EXISTS kpi_value_as_of_idx
    ON gold.kpi_value (as_of);

COMMENT ON TABLE gold.kpi_value IS
    'One row per KPI, business date and dimension. NULL value = the formula returned NULL; a '
    'failed formula writes no row here and a failed gold.kpi_run instead.';

-- One row per KPI per run, whatever happened. This is "the failure visible in run history
-- rather than only in logs" (delivery plan, R5 evidence): a broken formula's Postgres error
-- is written here verbatim, next to the rows that materialised in the same run.
CREATE TABLE IF NOT EXISTS gold.kpi_run (
    kpi_identifier   text        NOT NULL,
    run_id           text        NOT NULL,
    as_of            date        NOT NULL,
    status           text        NOT NULL,
    grain            text        NOT NULL,
    -- The columns the formula returned before its value column, in order -- what the view
    -- projects. Empty at grain overall.
    grain_keys       text[]      NOT NULL DEFAULT '{}',
    rows_written     integer     NOT NULL DEFAULT 0,
    -- `error` is the database's or the binder's own message, verbatim. `reason` is why a KPI
    -- was skipped (not ready, a rowset), or how a predicate was bound. `statement` is the
    -- assembled SQL the guard saw and the warehouse ran, so a failure can be reproduced by
    -- hand without re-deriving the binding.
    error            text,
    reason           text,
    statement        text,
    source_snapshot  text        NOT NULL,
    formula_sha256   text        NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz,
    PRIMARY KEY (kpi_identifier, run_id),
    CONSTRAINT kpi_run_status_known
        CHECK (status IN ('materialised', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS kpi_run_latest_idx
    ON gold.kpi_run (kpi_identifier, started_at DESC);

COMMENT ON TABLE gold.kpi_run IS
    'One row per KPI per materialisation run. status: materialised (value rows written), '
    'failed (bind, guard, compile or execution error -- see error), skipped (not ready, or a '
    'rowset -- see reason).';

-- The most recent run per KPI, with how long ago it finished. What the KPI health screen
-- reads for status/last_run_id/last_error; last_computed_at needs the latest *materialised*
-- run, which warehouse/health.py reads separately, because a KPI whose latest run failed
-- still has a last good value worth showing.
CREATE OR REPLACE VIEW gold.kpi_latest_run AS
SELECT DISTINCT ON (r.kpi_identifier)
    r.kpi_identifier,
    r.run_id,
    r.as_of,
    r.status,
    r.grain,
    r.grain_keys,
    r.rows_written,
    r.error,
    r.reason,
    r.source_snapshot,
    r.started_at,
    r.finished_at,
    CAST(EXTRACT(EPOCH FROM (now() - r.finished_at)) / 3600.0 AS numeric) AS staleness_hours
FROM gold.kpi_run AS r
ORDER BY r.kpi_identifier, r.started_at DESC;
