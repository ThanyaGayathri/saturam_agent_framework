-- Conformance for stream 'crm.deal' -> silver.crm_deal.
-- One statement. Parameter: :load_id.
-- The rules and their reasoning are documented once, in conform/crm_contact.sql.

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
        NULLIF(btrim(r.payload ->> 'stage'), '')        AS stage,
        NULLIF(btrim(r.payload ->> 'brand'), '')        AS brand,
        -- Known-dirty: absent on some quote-request deals today. Carried as NULL and
        -- reported; d_901 in the C1 fixtures is exactly this case.
        NULLIF(btrim(r.payload ->> 'quote_request_source_url'), '')
                                                        AS quote_request_source_url
    FROM landing.raw_row AS r
    WHERE r.stream_id = 'crm.deal'
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
INSERT INTO silver.crm_deal AS s (
    record_type, object_id, last_modified_at, stage, brand, quote_request_source_url,
    source_path, source_ingested_at, load_id, conformed_at
)
SELECT
    w.record_type, w.object_id, w.last_modified_at, w.stage, w.brand,
    w.quote_request_source_url,
    w.source_path, w.ingested_at, CAST(:load_id AS uuid), now()
FROM winner AS w
ON CONFLICT (record_type, object_id) DO UPDATE
SET last_modified_at         = EXCLUDED.last_modified_at,
    stage                    = EXCLUDED.stage,
    brand                    = EXCLUDED.brand,
    quote_request_source_url = EXCLUDED.quote_request_source_url,
    source_path              = EXCLUDED.source_path,
    source_ingested_at       = EXCLUDED.source_ingested_at,
    load_id                  = EXCLUDED.load_id,
    conformed_at             = EXCLUDED.conformed_at
WHERE EXCLUDED.last_modified_at > s.last_modified_at;
