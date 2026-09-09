-- Conformance for stream 'analytics.event' -> silver.analytics_event.
-- One statement. Parameter: :load_id.
-- The rules and their reasoning are documented once, in conform/crm_contact.sql.
--
-- This stream is the one exception to the shape of the other four, and the exception is
-- forced by the contract rather than chosen. Its watermark, occurred_at, is one of its key
-- columns. Two consequences:
--
--   1. No `<watermark> DESC` in the ORDER BY. Rows sharing the key necessarily share the
--      watermark, so there is no greater one to prefer; de-duplication degenerates to
--      duplicate collapse and the tie-break alone decides. Ordering by occurred_at DESC
--      after having already ordered by it as a key would be a no-op dressed up as a rule.
--   2. ON CONFLICT DO NOTHING rather than DO UPDATE ... WHERE. A newer version of the same
--      row cannot exist, because a changed occurred_at is a different row. Writing
--      DO UPDATE with a WHERE that can never be true would suggest an update path that
--      does not exist.
--
-- Both are correct for the domain: an event is immutable once it has happened. If analytics
-- ever needs correctable events, that is a contract change, not an edit here.

WITH typed AS (
    SELECT
        r.source_path,
        r.ingested_at,
        r.line_number,
        NULLIF(btrim(r.payload ->> 'user_id'), '')    AS user_id,
        NULLIF(btrim(r.payload ->> 'session_id'), '') AS session_id,
        NULLIF(btrim(r.payload ->> 'event_name'), '') AS event_name,
        silver.try_timestamptz(
            NULLIF(btrim(r.payload ->> 'occurred_at'), '')
        )                                             AS occurred_at,
        NULLIF(btrim(r.payload ->> 'brand'), '')      AS brand,
        NULLIF(btrim(r.payload ->> 'product_id'), '') AS product_id,
        NULLIF(btrim(r.payload ->> 'identity_key'), '')  AS identity_key
    FROM landing.raw_row AS r
    WHERE r.stream_id = 'analytics.event'
),
conforming AS (
    SELECT * FROM typed
    WHERE user_id IS NOT NULL
      AND session_id IS NOT NULL
      AND event_name IS NOT NULL
      AND occurred_at IS NOT NULL
),
winner AS (
    SELECT DISTINCT ON (user_id, session_id, event_name, occurred_at) *
    FROM conforming
    ORDER BY user_id, session_id, event_name, occurred_at,
             ingested_at ASC,
             line_number ASC
)
INSERT INTO silver.analytics_event (
    user_id, session_id, event_name, occurred_at, brand, product_id, identity_key,
    source_path, source_ingested_at, load_id, conformed_at
)
SELECT
    w.user_id, w.session_id, w.event_name, w.occurred_at, w.brand, w.product_id,
    w.identity_key,
    w.source_path, w.ingested_at, CAST(:load_id AS uuid), now()
FROM winner AS w
ON CONFLICT (user_id, session_id, event_name, occurred_at) DO NOTHING;
