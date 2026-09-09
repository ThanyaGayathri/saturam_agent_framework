-- silver.crm_contact -- conformed from stream 'crm.contact'.
--
-- record_type is part of the key, not decoration: CRM multiplexes contact, deal and
-- deal_stage_history into one file and one id space, so ('contact', 'c_500') and a deal that
-- happened to share the id are different rows.

CREATE TABLE IF NOT EXISTS silver.crm_contact (
    record_type        text        NOT NULL,
    object_id          text        NOT NULL,
    last_modified_at   timestamptz NOT NULL,
    -- Business columns are nullable across all five silver tables; only key and watermark
    -- columns are NOT NULL. A missing business value is reported as a defect and the row is
    -- kept, per the landing contract -- and a NOT NULL would abort the statement on one
    -- dirty row, dropping the load rather than the row.
    email              text,
    -- Known-dirty at source: free text or missing today, against an eventual enforced enum.
    brand              text,
    -- The shared identity hash (contract 1.1.0). Nullable and reserved; see analytics_event.
    identity_key       text,
    source_path        text        NOT NULL,
    source_ingested_at timestamptz NOT NULL,
    load_id            uuid        NOT NULL,
    conformed_at       timestamptz NOT NULL,
    PRIMARY KEY (record_type, object_id)
);

COMMENT ON COLUMN silver.crm_contact.brand IS
    'Known-dirty per LANDING_CONTRACT.md. Nullable so the row survives and the defect is '
    'reported in quality.row_defect. NOT NULL here would be dropping the row.';
