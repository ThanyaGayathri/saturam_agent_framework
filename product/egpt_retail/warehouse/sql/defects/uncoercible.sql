-- Rows whose numeric columns are present, non-blank, and will not coerce (R4).
-- One statement. Parameters: :paths, :streams, :columns, :kinds.
--
-- Closes the half of the coercion gap the landing contract does not. Contract 1.1.0 added
-- `timestamp_columns`, so a non-watermark timestamp like `entered_at` is now format-checked
-- by `validate_row` and reported before it ever reaches here. Numbers are not: nothing in the
-- contract says `rage_click_count` is a number, so a malformed count is not blank, draws no
-- defect, and would then be silently nulled by `silver.try_integer` in conformance --
-- contradicting 040_coercion.sql's claim that a NULL returned there is never silent.
--
-- Detected in SQL rather than in Python on purpose: it calls the very same `silver.try_*`
-- functions the conformance statements call, so "will not coerce" means exactly what it means
-- there. A Python reimplementation would be a second definition of coercible that has to
-- agree with the first, and eventually would not.
--
-- The column list is a parameter rather than baked in, so it stays derived from
-- warehouse/schema.py::COERCED_COLUMNS -- whose timestamps now come from the contract and
-- whose integers are R4's own -- and a test binds it to what the conform SQL actually wraps.

SELECT
    r.stream_id,
    r.source_path,
    r.line_number,
    c.column_name,
    r.payload ->> c.column_name AS raw_value
FROM landing.raw_row AS r
JOIN unnest(
        CAST(:streams AS text[]),
        CAST(:columns AS text[]),
        CAST(:kinds AS text[])
     ) AS c(stream_id, column_name, kind)
  ON c.stream_id = r.stream_id
WHERE r.source_path = ANY(CAST(:paths AS text[]))
  AND NULLIF(btrim(r.payload ->> c.column_name), '') IS NOT NULL
  AND (
        (c.kind = 'timestamptz'
         AND silver.try_timestamptz(r.payload ->> c.column_name) IS NULL)
     OR (c.kind = 'integer'
         AND silver.try_integer(r.payload ->> c.column_name) IS NULL)
      )
ORDER BY r.source_path, r.line_number, c.column_name;
