-- What each load measured, and whether it was allowed through (R4, G8).
--
-- These tables live in the warehouse rather than the control plane for two reasons. The
-- measurements are of warehouse data, produced by SQL running inside the warehouse; and
-- Postgres cannot join across databases, so R5's gold step -- which must not run when a load
-- was blocked -- has to be able to read the verdict in the same database it writes gold into.
-- The link back to control-plane run history is `load_run.workflow_run_id`.
--
-- Applied before 030_landing.sql: landing.raw_row.load_id references load_run.

CREATE TABLE IF NOT EXISTS quality.load_run (
    load_id         uuid        PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    -- 'manual' | 'schedule' | 'cookbook'. Free text rather than an enum: a new trigger is
    -- not a schema change, and this column is read by people more than by code.
    trigger         text        NOT NULL,
    workflow_run_id text,
    landing_root    text        NOT NULL,
    files_loaded    integer     NOT NULL DEFAULT 0,
    rows_landed     integer     NOT NULL DEFAULT 0,
    verdict         text,
    -- One paragraph, readable without opening a log. This is the artefact R4's evidence bar
    -- asks for; it is rendered by one pure function so the same text reaches this column,
    -- the workflow's run history, and the operator's terminal.
    reason          text,
    CONSTRAINT load_run_verdict_known
        CHECK (verdict IS NULL OR verdict IN ('passed', 'blocked', 'failed'))
);

COMMENT ON COLUMN quality.load_run.verdict IS
    'NULL while the load is in flight. ''blocked'' means a gate refused: silver is unchanged '
    'from the last good load and gold must not be rebuilt.';

CREATE TABLE IF NOT EXISTS quality.gate_result (
    load_id    uuid        NOT NULL REFERENCES quality.load_run (load_id) ON DELETE CASCADE,
    gate       text        NOT NULL,
    -- A stream_id, or an edge rendered as 'child.column -> parent.column'.
    subject    text        NOT NULL,
    measured   numeric,
    threshold  numeric,
    -- The whole measurement row, so the health screen can show what was seen without this
    -- table growing a column every time a gate learns to report one more number.
    observed   jsonb       NOT NULL,
    passed     boolean     NOT NULL,
    reason     text        NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now(),
    -- Retry-safe: a step re-run upserts its verdict rather than doubling the rows the
    -- aggregate reads. Step.max_retries defaults to 3, so this is not hypothetical.
    PRIMARY KEY (load_id, gate, subject)
);

CREATE TABLE IF NOT EXISTS quality.row_defect (
    defect_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id     uuid    NOT NULL REFERENCES quality.load_run (load_id) ON DELETE CASCADE,
    -- NULL when the row's record_type was absent or unknown, so the stream could not be
    -- resolved at all. That is itself the defect, recorded as 'unknown_stream'.
    stream_id   text,
    source_path text    NOT NULL,
    line_number integer NOT NULL,
    -- The key tuple rendered for a human, e.g. 'contact/c_501'. NULL when a key column is
    -- itself the thing that is missing.
    row_key     text,
    column_name text,
    defect_kind text    NOT NULL,
    detail      text    NOT NULL,
    -- true when the defect is in a key or the watermark, which means the row cannot be
    -- identified and is excluded from silver. false for a known-dirty business column, whose
    -- row is conformed with a NULL and reported here -- never dropped, per the contract.
    structural  boolean NOT NULL,
    CONSTRAINT row_defect_kind_known CHECK (defect_kind IN (
        'missing_column', 'null_or_blank', 'uncoercible', 'unknown_stream', 'not_an_object'
    ))
);

CREATE INDEX IF NOT EXISTS row_defect_load_idx ON quality.row_defect (load_id);
CREATE INDEX IF NOT EXISTS row_defect_stream_idx ON quality.row_defect (stream_id, load_id);

CREATE TABLE IF NOT EXISTS quality.load_stream_count (
    load_id        uuid    NOT NULL REFERENCES quality.load_run (load_id) ON DELETE CASCADE,
    stream_id      text    NOT NULL,
    -- Rows the files this load covered CARRIED for the stream -- not rows newly inserted.
    -- The distinction is load-bearing. Landing is idempotent, so re-loading the same files
    -- inserts nothing; if this counted insertions, the second of two identical loads would
    -- report zero rows and the row-count gate would block it, which is exactly the scenario
    -- R4 has to prove passes. What the gate wants to know is how big the pull was, and a
    -- re-delivered window is the same size as the window it repeats.
    landed_rows    integer NOT NULL,
    -- Rows that were actually new. Zero on a re-load, which is the evidence for "the
    -- duplicate load changes no counts" -- reported, never gated on.
    inserted_rows  integer NOT NULL DEFAULT 0,
    -- Rows that survived structural validation and reached the dedup.
    conformed_rows integer NOT NULL,
    -- The files this load READ rows for, per stream. Recorded by the loader rather than
    -- derived from landing.raw_row, because a re-load inserts nothing (ON CONFLICT DO
    -- NOTHING) and so has no rows carrying its own load_id -- deriving provenance from
    -- insertions would report "no file" for every stream of an idempotent re-load, and
    -- would make coverage look empty when the pull was in fact complete.
    source_paths   text[]  NOT NULL DEFAULT '{}',
    -- Total rows in the silver table after this load.
    silver_rows    integer NOT NULL,
    PRIMARY KEY (load_id, stream_id)
);
