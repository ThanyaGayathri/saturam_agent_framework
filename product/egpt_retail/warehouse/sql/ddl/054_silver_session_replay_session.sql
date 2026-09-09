-- silver.session_replay_session -- conformed from stream 'session_replay.session'.
--
-- The only stream with nullable_columns in the contract, and the distinction matters: an
-- unset identity here is not dirty data, it is the truth. A session can end before login, or
-- be abandoned before submission. The two known-dirty CRM columns are nullable because the
-- source is wrong; these two are nullable because the world is.

CREATE TABLE IF NOT EXISTS silver.session_replay_session (
    session_id         text        NOT NULL,
    -- The watermark since contract 1.1.0. session_start_at never changes for a session, so a
    -- re-pull carrying corrected click counts used to tie and lose; Lane C added this column
    -- so a correction can win. R4 raised that; this is the fix landing.
    last_updated_at    timestamptz NOT NULL,
    -- No longer the watermark, so no longer structural: a business column like any other.
    session_start_at   timestamptz,
    -- Business columns nullable, key and watermark NOT NULL -- see silver.crm_contact.
    rage_click_count   integer,
    dead_click_count   integer,
    script_error_count integer,
    session_url        text,
    -- Legitimately unknown, per LANDING_SCHEMAS['session_replay.session'].nullable_columns.
    identity_at_login  text,
    identity_at_submit text,
    -- The shared identity hash (contract 1.1.0). Nullable and reserved; see analytics_event.
    identity_key       text,
    source_path        text        NOT NULL,
    source_ingested_at timestamptz NOT NULL,
    load_id            uuid        NOT NULL,
    conformed_at       timestamptz NOT NULL,
    PRIMARY KEY (session_id)
);

COMMENT ON COLUMN silver.session_replay_session.session_start_at IS
    'When the session began. NOT the watermark: it never changes, so a re-pull carrying '
    'corrected click counts would tie and the earliest pull would win, discarding the '
    'correction. Landing contract 1.1.0 added last_updated_at for that reason, and records '
    'this one as the superseded watermark.';
COMMENT ON COLUMN silver.session_replay_session.identity_at_login IS
    'NULL when the session ended before login. Legitimately unknown, not a defect.';
