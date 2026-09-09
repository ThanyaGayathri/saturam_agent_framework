-- silver.analytics_event -- conformed from stream 'analytics.event'.
--
-- Keys and required columns are LANDING_SCHEMAS['analytics.event']; the four trailing
-- columns are provenance, carried on every silver row so R5 can satisfy "every gold row
-- carries its source and computation time" by propagating them rather than re-deriving.
--
-- Its watermark, occurred_at, is *inside* its key, so a re-pull can never present a newer
-- version of the same row -- dedup degenerates to duplicate collapse, which is correct,
-- because an event is immutable once it has happened.
--
-- Nullability follows one rule across all five silver tables, and it is the landing
-- contract's own structural/business split: key and watermark columns are NOT NULL, because
-- a row missing one cannot be identified and is excluded from silver entirely. Every other
-- required column is nullable, because the contract says a missing business value is
-- reported as a defect and never dropped -- and a NOT NULL here would abort the whole
-- statement on one dirty row, which is dropping the entire load rather than the row.

CREATE TABLE IF NOT EXISTS silver.analytics_event (
    user_id            text        NOT NULL,
    session_id         text        NOT NULL,
    event_name         text        NOT NULL,
    occurred_at        timestamptz NOT NULL,
    brand              text,
    product_id         text,
    -- The shared identity hash (contract 1.1.0). Nullable and reserved: it is gated on privacy
    -- sign-off, so until it is populated nothing joins across sources.
    identity_key       text,
    source_path        text        NOT NULL,
    source_ingested_at timestamptz NOT NULL,
    load_id            uuid        NOT NULL,
    conformed_at       timestamptz NOT NULL,
    PRIMARY KEY (user_id, session_id, event_name, occurred_at)
);

COMMENT ON TABLE silver.analytics_event IS
    'One row per event. Three RFQ funnel events (add_to_cart, form_start, submit) are not '
    'instrumented at source yet, so a short funnel here is a conforming file, not a defect.';
COMMENT ON COLUMN silver.analytics_event.session_id IS
    'The analytics vendor''s session id. NOT joinable to '
    'silver.session_replay_session.session_id -- the two sources use separate id spaces and '
    'no bridge exists yet. Raised as a Lane C finding; R10''s friction join needs it.';
