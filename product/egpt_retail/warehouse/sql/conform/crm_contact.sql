-- Conformance for stream 'crm.contact' -> silver.crm_contact.
-- One statement. Parameter: :load_id.
--
-- Run it by hand exactly as the loader does:
--   psql "$EGPT_RETAIL_WAREHOUSE_DB_URL" \
--     -v load_id="'3f2c8e14-0000-0000-0000-000000000000'" \
--     -f product/egpt_retail/warehouse/sql/conform/crm_contact.sql
--
-- Reads all of landing for the stream every run -- a full recompute, not an incremental
-- pass. That is what makes "the duplicate load changes no counts" true by construction: the
-- result depends only on landing's contents, so re-running produces the same winner, the
-- ON CONFLICT guard finds nothing newer, and not even conformed_at moves.

WITH typed AS (
    -- Extract and coerce. NULLIF(btrim(...), '') collapses absent, JSON-null and
    -- blank-string into one NULL, because the contract treats a whitespace-only value as
    -- dirty rather than letting a connector paper over a gap with "".
    SELECT
        r.source_path,
        r.ingested_at,
        r.line_number,
        NULLIF(btrim(r.payload ->> 'record_type'), '')      AS record_type,
        NULLIF(btrim(r.payload ->> 'object_id'), '')        AS object_id,
        silver.try_timestamptz(
            NULLIF(btrim(r.payload ->> 'last_modified_at'), '')
        )                                                   AS last_modified_at,
        NULLIF(btrim(r.payload ->> 'email'), '')            AS email,
        NULLIF(btrim(r.payload ->> 'brand'), '')            AS brand,
        NULLIF(btrim(r.payload ->> 'identity_key'), '')     AS identity_key
    FROM landing.raw_row AS r
    WHERE r.stream_id = 'crm.contact'
),
conforming AS (
    -- Structural columns only: a row missing a key or the watermark cannot be identified,
    -- so it is excluded here and reported as a structural defect. Business columns are not
    -- filtered on -- a null brand is carried into silver and reported, never dropped.
    SELECT * FROM typed
    WHERE record_type IS NOT NULL
      AND object_id IS NOT NULL
      AND last_modified_at IS NOT NULL
),
winner AS (
    -- The landing contract's de-duplication rule, executable. Group by the key columns and
    -- keep the greatest watermark; on an exact watermark tie take the earliest-pulled file,
    -- and within one file the first line folded. Deterministic, and the same answer
    -- cookbook/94_retail/C01_landing_contract.py asserts in Python on h_9002.
    SELECT DISTINCT ON (record_type, object_id) *
    FROM conforming
    ORDER BY record_type, object_id,
             last_modified_at DESC,
             ingested_at      ASC,
             line_number      ASC
)
INSERT INTO silver.crm_contact AS s (
    record_type, object_id, last_modified_at, email, brand, identity_key,
    source_path, source_ingested_at, load_id, conformed_at
)
SELECT
    w.record_type, w.object_id, w.last_modified_at, w.email, w.brand, w.identity_key,
    w.source_path, w.ingested_at, CAST(:load_id AS uuid), now()
FROM winner AS w
ON CONFLICT (record_type, object_id) DO UPDATE
SET last_modified_at   = EXCLUDED.last_modified_at,
    email              = EXCLUDED.email,
    brand              = EXCLUDED.brand,
    identity_key       = EXCLUDED.identity_key,
    source_path        = EXCLUDED.source_path,
    source_ingested_at = EXCLUDED.source_ingested_at,
    load_id            = EXCLUDED.load_id,
    conformed_at       = EXCLUDED.conformed_at
-- The late-arriving-row guard: the same strictly-greater rule, promoted from within one run
-- to across runs. A later load carrying a row older than what silver holds updates nothing;
-- one carrying an equal watermark also updates nothing, which is the tie-break staying
-- stable across re-runs rather than flapping between two equally-timestamped versions.
WHERE EXCLUDED.last_modified_at > s.last_modified_at;
