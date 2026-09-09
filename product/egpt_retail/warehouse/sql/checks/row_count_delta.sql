-- Measurement for the row-count-delta gate (R4, G8).
-- One statement. Parameters: :load_id, :previous_load_id.
--
-- Measures only. There is no threshold in this file, and that is deliberate: an analyst can
-- run it in psql to see what a night looked like without needing to know the policy, and the
-- permit/block decision stays in the executor, where the alignment rule puts it.
--
-- Two numbers per stream, because they catch different failures.
--
--   landed_rows  -- what the pull brought. This is the undersized-pull signal, and it is the
--                   only place it is visible: raw is append-only and conformance is a full
--                   recompute, so a short pull does not shrink silver, it just quietly stops
--                   adding to it.
--   silver_rows  -- the total after conformance. Cannot decrease under the above, so any
--                   decrease means something other than a short pull went wrong, and is
--                   worth blocking on with no tolerance at all.
--
-- prev_* are NULL on the first load. The executor records that as 'no_baseline' and passes
-- explicitly, as a persisted row -- a first load must not look the same as a load nobody
-- checked.
--
-- Whether a stream was part of this pull is `landed_rows > 0`, and that is deliberately NOT
-- derived from which rows carry this load_id in landing.raw_row: an idempotent re-load
-- inserts nothing, so such a test would call a complete re-pull an empty one. It is also per
-- STREAM rather than per source -- CRM multiplexes three streams into one file, so a window
-- in which only contacts changed is a legitimate pull, not two streams that went missing.

SELECT
    c.stream_id,
    c.landed_rows,
    c.conformed_rows,
    c.silver_rows,
    c.source_paths,
    p.landed_rows AS prev_landed_rows,
    p.silver_rows AS prev_silver_rows
FROM quality.load_stream_count AS c
LEFT JOIN quality.load_stream_count AS p
       ON p.stream_id = c.stream_id
      AND p.load_id = CAST(:previous_load_id AS uuid)
WHERE c.load_id = CAST(:load_id AS uuid)
ORDER BY c.stream_id;
