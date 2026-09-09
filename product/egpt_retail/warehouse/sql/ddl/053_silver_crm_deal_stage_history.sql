-- silver.crm_deal_stage_history -- conformed from stream 'crm.deal_stage_history'.
--
-- The child side of R4's one referential-integrity edge. Reconstructing funnel timing is
-- what this stream exists for, so an orphaned transition is a real signal rather than noise.

CREATE TABLE IF NOT EXISTS silver.crm_deal_stage_history (
    record_type        text        NOT NULL,
    object_id          text        NOT NULL,
    last_modified_at   timestamptz NOT NULL,
    -- Business columns nullable, key and watermark NOT NULL -- see silver.crm_contact. A
    -- NULL deal_id is a reported defect; it is not counted as an orphan by the referential
    -- integrity gate, which measures rows that name a deal that does not exist.
    deal_id            text,
    stage              text,
    entered_at         timestamptz,
    source_path        text        NOT NULL,
    source_ingested_at timestamptz NOT NULL,
    load_id            uuid        NOT NULL,
    conformed_at       timestamptz NOT NULL,
    PRIMARY KEY (record_type, object_id)
);

CREATE INDEX IF NOT EXISTS crm_deal_stage_history_deal_idx
    ON silver.crm_deal_stage_history (deal_id);

COMMENT ON COLUMN silver.crm_deal_stage_history.deal_id IS
    'References silver.crm_deal.object_id. Deliberately NOT a FOREIGN KEY. A constraint '
    'would reject the child row -- dropping data the landing contract says to report -- and '
    'would turn a legitimate late-arriving parent into a load failure. G8 asks for a check '
    'that gates gold, not one that refuses silver, so the check_referential_integrity gate '
    'counts and names the orphans instead. See warehouse/LOAD_CONTRACT.md.';
