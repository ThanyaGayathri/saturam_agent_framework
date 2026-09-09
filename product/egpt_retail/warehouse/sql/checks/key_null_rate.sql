-- Measurement for the key-null-rate gate (R4, G8: "null rates on key columns").
-- One statement. Parameter: :load_id.
--
-- Reads quality.row_defect rather than re-deriving nulls from landing. The defects were
-- written at load time by contracts.landing.validate_row -- the landing contract's own
-- definition of a conforming row -- so this gate measures exactly what the contract says is
-- broken, and cannot drift from it. Restating that rule in SQL would create a second
-- definition of "defect" that has to be kept in agreement with the first.
--
-- `structural` is the contract's key/watermark distinction: those columns identify the row,
-- so a defect in one means the row cannot enter silver at all. That silent exclusion is the
-- thing this gate exists to make loud -- without it, a load of nulled keys would land, drop
-- every row on the floor, and report a clean run.
--
-- Business-column defects (a known-dirty brand, an absent quote URL) are counted here too,
-- but with structural = false, so the executor can report them without blocking on them.

SELECT
    c.stream_id,
    d.column_name,
    d.structural,
    COUNT(*)        AS defect_rows,
    c.landed_rows,
    CASE
        WHEN c.landed_rows = 0 THEN CAST(0 AS numeric)
        ELSE ROUND(CAST(COUNT(*) AS numeric) / c.landed_rows, 6)
    END             AS defect_rate
FROM quality.row_defect AS d
JOIN quality.load_stream_count AS c
  ON c.load_id = d.load_id
 AND c.stream_id = d.stream_id
WHERE d.load_id = CAST(:load_id AS uuid)
  AND d.column_name IS NOT NULL
GROUP BY c.stream_id, d.column_name, d.structural, c.landed_rows
ORDER BY d.structural DESC, c.stream_id, d.column_name;
