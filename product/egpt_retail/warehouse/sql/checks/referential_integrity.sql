-- Measurement for the referential-integrity gate (R4, G8).
-- One statement. No parameters -- it measures silver as it now stands, not one load's slice,
-- because an orphan is a property of the whole entity set and a parent may have arrived on
-- any earlier load.
--
-- One branch per declared edge. Adding an edge is adding a UNION ALL branch to this file:
-- no Python, no seed table, no lifecycle. Each branch names itself, so the executor's reason
-- string can quote the edge without knowing anything about the schema.
--
-- Why this is a gate and not a FOREIGN KEY: a constraint would reject the child row, which
-- drops data the landing contract says to report, and would turn a legitimate late-arriving
-- parent into a load failure. G8 asks for a check that blocks gold, not one that refuses
-- silver. See silver.crm_deal_stage_history.deal_id's column comment.
--
-- Edges deliberately NOT checked, recorded so the absence is a decision rather than an
-- oversight:
--
--   analytics_event.session_id -> session_replay_session.session_id. The two sources use
--   separate id spaces (s_2001 versus sess_300) and no bridge exists yet, so this would
--   report a 100% breach against perfectly conforming data. Raised as a Lane C finding; the
--   shared identity key that would fix it is gated on privacy sign-off, and R10's friction
--   join needs it.
--
--   crm_contact.email -> session_replay_session.identity_at_*. Identity resolution, not
--   referential integrity.

SELECT
    'crm.deal_stage_history.deal_id -> crm.deal.object_id' AS edge,
    COUNT(*)                                               AS child_rows,
    COUNT(*) FILTER (WHERE p.object_id IS NULL)            AS orphan_rows,
    CASE
        WHEN COUNT(*) = 0 THEN CAST(0 AS numeric)
        ELSE ROUND(
            CAST(COUNT(*) FILTER (WHERE p.object_id IS NULL) AS numeric) / COUNT(*), 6
        )
    END                                                    AS orphan_rate,
    -- A bounded sample, so the reason can name offenders instead of only counting them.
    (ARRAY_AGG(c.deal_id) FILTER (WHERE p.object_id IS NULL))[1:5] AS sample_orphans
FROM silver.crm_deal_stage_history AS c
LEFT JOIN silver.crm_deal AS p ON p.object_id = c.deal_id
-- A NULL deal_id is a reported row defect, not an orphan: the row does not name a deal that
-- is missing, it fails to name one at all. Counting it here would double-report it and would
-- make this gate fire for a reason the key-null-rate gate already covers.
WHERE c.deal_id IS NOT NULL;
