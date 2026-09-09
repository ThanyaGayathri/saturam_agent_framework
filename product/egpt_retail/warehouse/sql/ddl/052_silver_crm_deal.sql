-- silver.crm_deal -- conformed from stream 'crm.deal'.

CREATE TABLE IF NOT EXISTS silver.crm_deal (
    record_type              text        NOT NULL,
    object_id                text        NOT NULL,
    last_modified_at         timestamptz NOT NULL,
    -- Business columns nullable, key and watermark NOT NULL -- see silver.crm_contact.
    stage                    text,
    -- Required on deals since contract 1.1.0, and known-dirty for the same reason it is on
    -- contacts: free text or missing today against an eventual enforced enum.
    brand                    text,
    -- Known-dirty at source: inconsistently populated on quote-request deals today.
    quote_request_source_url text,
    source_path              text        NOT NULL,
    source_ingested_at       timestamptz NOT NULL,
    load_id                  uuid        NOT NULL,
    conformed_at             timestamptz NOT NULL,
    PRIMARY KEY (record_type, object_id)
);

-- The parent side of the one referential-integrity edge R4 checks. A UNIQUE index rather
-- than a FOREIGN KEY on the child: see silver.crm_deal_stage_history.deal_id for why.
CREATE UNIQUE INDEX IF NOT EXISTS crm_deal_object_id_key ON silver.crm_deal (object_id);

COMMENT ON COLUMN silver.crm_deal.quote_request_source_url IS
    'Known-dirty per LANDING_CONTRACT.md. Nullable so the row survives and the defect is '
    'reported in quality.row_defect.';
