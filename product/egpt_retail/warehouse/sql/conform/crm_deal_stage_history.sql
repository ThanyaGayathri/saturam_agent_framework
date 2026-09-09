-- Conformance for stream 'crm.deal_stage_history' -> silver.crm_deal_stage_history.
-- One statement. Parameter: :load_id.
-- The rules and their reasoning are documented once, in conform/crm_contact.sql.
--
-- h_9002 in the C1 fixtures is the exact-watermark-tie case: both pulls carry
-- last_modified_at 2026-09-01T07:20:00Z with a different stage. The ingested_at ASC
-- tie-break makes the earlier pull win, deterministically -- 'quote_requested', not
-- 'quote_sent'. That is the contract's stated resolution, and R4 must reproduce it.

WITH typed AS (
    SELECT
        r.source_path,
        r.ingested_at,
        r.line_number,
        NULLIF(btrim(r.payload ->> 'record_type'), '')  AS record_type,
        NULLIF(btrim(r.payload ->> 'object_id'), '')    AS object_id,
        silver.try_timestamptz(
            NULLIF(btrim(r.payload ->> 'last_modified_at'), '')
        )                                               AS last_modified_at,
        NULLIF(btrim(r.payload ->> 'deal_id'), '')      AS deal_id,
        NULLIF(btrim(r.payload ->> 'stage'), '')        AS stage,
        silver.try_timestamptz(
            NULLIF(btrim(r.payload ->> 'entered_at'), '')
        )                                               AS entered_at
    FROM landing.raw_row AS r
    WHERE r.stream_id = 'crm.deal_stage_history'
),
conforming AS (
    SELECT * FROM typed
    WHERE record_type IS NOT NULL
      AND object_id IS NOT NULL
      AND last_modified_at IS NOT NULL
),
winner AS (
    SELECT DISTINCT ON (record_type, object_id) *
    FROM conforming
    ORDER BY record_type, object_id,
             last_modified_at DESC,
             ingested_at      ASC,
             line_number      ASC
)
INSERT INTO silver.crm_deal_stage_history AS s (
    record_type, object_id, last_modified_at, deal_id, stage, entered_at,
    source_path, source_ingested_at, load_id, conformed_at
)
SELECT
    w.record_type, w.object_id, w.last_modified_at, w.deal_id, w.stage, w.entered_at,
    w.source_path, w.ingested_at, CAST(:load_id AS uuid), now()
FROM winner AS w
ON CONFLICT (record_type, object_id) DO UPDATE
SET last_modified_at   = EXCLUDED.last_modified_at,
    deal_id            = EXCLUDED.deal_id,
    stage              = EXCLUDED.stage,
    entered_at         = EXCLUDED.entered_at,
    source_path        = EXCLUDED.source_path,
    source_ingested_at = EXCLUDED.source_ingested_at,
    load_id            = EXCLUDED.load_id,
    conformed_at       = EXCLUDED.conformed_at
WHERE EXCLUDED.last_modified_at > s.last_modified_at;
