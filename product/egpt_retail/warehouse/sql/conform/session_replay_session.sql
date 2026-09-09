-- Conformance for stream 'session_replay.session' -> silver.session_replay_session.
-- One statement. Parameter: :load_id.
-- The rules and their reasoning are documented once, in conform/crm_contact.sql.
--
-- The watermark is `last_updated_at`, added by landing contract 1.1.0. Before that it was
-- `session_start_at`, which never changes for a session, so a re-pull carrying corrected
-- click counts tied on the watermark and the earliest pull won -- discarding the correction.
-- R4 raised that as a contract defect rather than working around it here, because ordering
-- one stream by ingested_at DESC would have given the five entities silently different
-- de-duplication semantics. Lane C fixed it in the contract; `session_start_at` is now a
-- business column, and the contract records it as the superseded watermark.

WITH typed AS (
    SELECT
        r.source_path,
        r.ingested_at,
        r.line_number,
        NULLIF(btrim(r.payload ->> 'session_id'), '')  AS session_id,
        silver.try_timestamptz(
            NULLIF(btrim(r.payload ->> 'last_updated_at'), '')
        )                                              AS last_updated_at,
        silver.try_timestamptz(
            NULLIF(btrim(r.payload ->> 'session_start_at'), '')
        )                                              AS session_start_at,
        silver.try_integer(
            NULLIF(btrim(r.payload ->> 'rage_click_count'), '')
        )                                              AS rage_click_count,
        silver.try_integer(
            NULLIF(btrim(r.payload ->> 'dead_click_count'), '')
        )                                              AS dead_click_count,
        silver.try_integer(
            NULLIF(btrim(r.payload ->> 'script_error_count'), '')
        )                                              AS script_error_count,
        NULLIF(btrim(r.payload ->> 'session_url'), '') AS session_url,
        -- Legitimately unknown when the session ended before login or before submission.
        -- These two are the contract's nullable_columns: a NULL here is not a defect and is
        -- not reported as one.
        NULLIF(btrim(r.payload ->> 'identity_at_login'), '')  AS identity_at_login,
        NULLIF(btrim(r.payload ->> 'identity_at_submit'), '') AS identity_at_submit,
        NULLIF(btrim(r.payload ->> 'identity_key'), '')        AS identity_key
    FROM landing.raw_row AS r
    WHERE r.stream_id = 'session_replay.session'
),
conforming AS (
    SELECT * FROM typed
    WHERE session_id IS NOT NULL
      AND last_updated_at IS NOT NULL
),
winner AS (
    SELECT DISTINCT ON (session_id) *
    FROM conforming
    ORDER BY session_id,
             last_updated_at DESC,
             ingested_at     ASC,
             line_number     ASC
)
INSERT INTO silver.session_replay_session AS s (
    session_id, last_updated_at, session_start_at, rage_click_count, dead_click_count,
    script_error_count, session_url, identity_at_login, identity_at_submit, identity_key,
    source_path, source_ingested_at, load_id, conformed_at
)
SELECT
    w.session_id, w.last_updated_at, w.session_start_at, w.rage_click_count,
    w.dead_click_count, w.script_error_count, w.session_url, w.identity_at_login,
    w.identity_at_submit, w.identity_key,
    w.source_path, w.ingested_at, CAST(:load_id AS uuid), now()
FROM winner AS w
ON CONFLICT (session_id) DO UPDATE
SET last_updated_at     = EXCLUDED.last_updated_at,
    session_start_at    = EXCLUDED.session_start_at,
    rage_click_count    = EXCLUDED.rage_click_count,
    dead_click_count    = EXCLUDED.dead_click_count,
    script_error_count  = EXCLUDED.script_error_count,
    session_url         = EXCLUDED.session_url,
    identity_at_login   = EXCLUDED.identity_at_login,
    identity_at_submit  = EXCLUDED.identity_at_submit,
    identity_key        = EXCLUDED.identity_key,
    source_path         = EXCLUDED.source_path,
    source_ingested_at  = EXCLUDED.source_ingested_at,
    load_id             = EXCLUDED.load_id,
    conformed_at        = EXCLUDED.conformed_at
WHERE EXCLUDED.last_updated_at > s.last_updated_at;
