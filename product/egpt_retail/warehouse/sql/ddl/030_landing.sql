-- Stage 1 Landing: raw rows as they arrived (R4, D4).
--
-- One table, not five. Landing holds rows "as-is with ingest metadata" per the landing
-- contract's own scope section, so nothing is parsed here -- one NDJSON line becomes one
-- jsonb value. Typing at this stage would put coercion before the row is durable and would
-- duplicate silver's job. And CRM multiplexes three streams into one file, so per-stream
-- landing tables would force the routing decision into Python: a transformation, in code,
-- which D6 rules out.

CREATE TABLE IF NOT EXISTS landing.raw_row (
    -- Landing-root-relative, exactly as contracts.landing.parse_landing_path accepts it:
    -- 'raw/crm/dt=2026-09-01/crm-20260901T060000Z.ndjson'. Never bucket-prefixed.
    source_path text        NOT NULL,
    line_number integer     NOT NULL,
    source      text        NOT NULL,
    dt          date        NOT NULL,
    -- From the FILENAME, never the object's storage mtime. The contract puts the timestamp
    -- in the name precisely so it survives a re-upload, and it is the fold order dedup
    -- depends on -- an mtime would silently reorder a round-tripped copy.
    ingested_at timestamptz NOT NULL,
    payload     jsonb       NOT NULL,
    load_id     uuid        NOT NULL REFERENCES quality.load_run (load_id),
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    -- CRM's routing rule, in SQL rather than in Python. All three operators are IMMUTABLE,
    -- which a generated column requires.
    --
    -- An unknown record_type yields NULL here rather than raising. That is deliberate:
    -- contracts.landing.resolve_stream_id raises ValueError, which would abort an entire
    -- file for one bad row. A NULL stream_id is a row-level defect ('unknown_stream'),
    -- reported like every other one -- data, not an exception.
    stream_id   text GENERATED ALWAYS AS (
        CASE
            WHEN source = 'analytics'      THEN 'analytics.event'
            WHEN source = 'session_replay' THEN 'session_replay.session'
            -- The CRM record types are enumerated rather than concatenated blindly. An
            -- unrecognised one must yield NULL, not 'crm.<whatever>': a bogus stream_id
            -- matches no conformance statement, so the row would be skipped by all five and
            -- disappear without anything noticing. NULL is the honest marker, and the loader
            -- reports it as an 'unknown_stream' defect.
            WHEN source = 'crm'
             AND payload ->> 'record_type' IN ('contact', 'deal', 'deal_stage_history')
                THEN 'crm.' || (payload ->> 'record_type')
        END
    ) STORED,
    -- Idempotence, and the reason "the duplicate load changes no counts" is structural here
    -- rather than hoped for. Raw is immutable and append-only, and the contract puts
    -- ingested_at in the filename so a re-pull cannot collide with an earlier one -- so a
    -- path plus a line number names exactly one row, for ever. The loader inserts with
    -- ON CONFLICT DO NOTHING, and re-loading the same files inserts zero rows.
    PRIMARY KEY (source_path, line_number)
);

-- The fold order dedup reads: within a stream, oldest-pulled file first, then file order.
CREATE INDEX IF NOT EXISTS raw_row_stream_fold_idx
    ON landing.raw_row (stream_id, ingested_at, line_number);

CREATE INDEX IF NOT EXISTS raw_row_load_idx ON landing.raw_row (load_id);

COMMENT ON TABLE landing.raw_row IS
    'Stage 1. Append-only. A re-pulled window lands a new file and therefore new rows here; '
    'de-duplication happens downstream, in the conformance SQL, per LANDING_CONTRACT.md.';
