-- =====================================================================
-- SM-R10 -- Bi-temporal edge model: Postgres DDL (relational / graph-lite tier)
-- Feeds SM-G4 (edges), SM-G5 (invalidation), SM-X11 (persistence).
--
-- Time representation: timestamptz (native interval math + btree/BRIN indexable).
-- The Python reference model mirrors these as epoch-seconds ints (order-equivalent).
-- Open intervals are NULL upper bounds, coalesced to 'infinity' in the predicates.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Bi-temporal edge table (NEW / greenfield -- no seed schema to extend)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_edge (
    edge_id     TEXT        PRIMARY KEY,
    subject_id  TEXT        NOT NULL,
    predicate   TEXT        NOT NULL,
    object_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,            -- tenant scope
    fact        TEXT,                            -- raw natural-language fact (optional)
    embedding   BYTEA,                           -- optional; real vector type lives in SM-G/R3 tier

    -- valid-time axis (when the fact was true in the world)
    t_valid     TIMESTAMPTZ NOT NULL,            -- valid-from
    t_invalid   TIMESTAMPTZ,                     -- valid-to; NULL = still true (open)

    -- transaction-time axis (when the system held the row as belief)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- transaction-from (immutable)
    expired_at  TIMESTAMPTZ,                         -- transaction-to; NULL = current belief

    CONSTRAINT memory_edge_valid_order   CHECK (t_invalid  IS NULL OR t_invalid  >= t_valid),
    CONSTRAINT memory_edge_txn_order     CHECK (expired_at IS NULL OR expired_at >= created_at)
);

-- Point-in-time lookups: identity triple + current-belief filter.
CREATE INDEX IF NOT EXISTS memory_edge_identity_idx
    ON memory_edge (user_id, subject_id, predicate);
-- Partial index over currently-believed rows (the hot 5a path).
CREATE INDEX IF NOT EXISTS memory_edge_current_idx
    ON memory_edge (user_id, subject_id, predicate, t_valid)
    WHERE expired_at IS NULL;
-- Valid-time range scans.
CREATE INDEX IF NOT EXISTS memory_edge_valid_idx  ON memory_edge (t_valid, t_invalid);

-- ---------------------------------------------------------------------
-- 2. Invalidate-not-delete: R1 supersede as a SQL-native function.
--    Transaction-time-versioned (SCD-2): a conflicting current fact whose valid window
--    CONTAINS p_t_valid is expired (transaction axis) and a bounded copy is re-inserted, so
--    both current-belief point-in-time (5a) and past-belief reconstruction (5b) hold. Nothing
--    is deleted or mutated in place. Uses now() as the transaction time (real ingestion).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION bt_upsert_edge(
    p_edge_id    TEXT,
    p_subject_id TEXT,
    p_predicate  TEXT,
    p_object_id  TEXT,
    p_user_id    TEXT,
    p_fact       TEXT,
    p_t_valid    TIMESTAMPTZ
) RETURNS TEXT AS $$
DECLARE
    v_count INT := 0;
    v_bound TIMESTAMPTZ;
BEGIN
    -- NOOP: identical fact already believed over a window covering p_t_valid.
    IF EXISTS (
        SELECT 1 FROM memory_edge
        WHERE user_id = p_user_id AND subject_id = p_subject_id AND predicate = p_predicate
          AND object_id = p_object_id AND expired_at IS NULL
          AND t_valid <= p_t_valid AND p_t_valid < COALESCE(t_invalid, 'infinity'::timestamptz)
    ) THEN
        RETURN 'noop';
    END IF;

    -- R1 SUPERSEDE: expire conflicting current facts whose window contains p_t_valid,
    -- then re-insert each as a bounded version (t_invalid = p_t_valid).
    WITH conflicting AS (
        SELECT edge_id, subject_id, predicate, object_id, user_id, fact, t_valid
        FROM memory_edge
        WHERE user_id = p_user_id AND subject_id = p_subject_id AND predicate = p_predicate
          AND object_id <> p_object_id AND expired_at IS NULL
          AND t_valid <= p_t_valid AND p_t_valid < COALESCE(t_invalid, 'infinity'::timestamptz)
    ),
    expired AS (
        UPDATE memory_edge m SET expired_at = now()
          FROM conflicting c WHERE m.edge_id = c.edge_id
        RETURNING c.subject_id, c.predicate, c.object_id, c.user_id, c.fact, c.t_valid
    ),
    reinserted AS (
        INSERT INTO memory_edge (edge_id, subject_id, predicate, object_id, user_id, fact,
                                 t_valid, t_invalid, created_at, expired_at)
        SELECT gen_random_uuid()::text, subject_id, predicate, object_id, user_id, fact,
               t_valid, p_t_valid, now(), NULL
        FROM expired
        RETURNING 1
    )
    SELECT count(*) INTO v_count FROM reinserted;

    -- Back-dated insert: bound the new edge to the earliest later conflicting fact, if any.
    SELECT min(t_valid) INTO v_bound FROM memory_edge
     WHERE user_id = p_user_id AND subject_id = p_subject_id AND predicate = p_predicate
       AND object_id <> p_object_id AND expired_at IS NULL AND t_valid > p_t_valid;

    INSERT INTO memory_edge (edge_id, subject_id, predicate, object_id, user_id, fact, t_valid, t_invalid)
    VALUES (p_edge_id, p_subject_id, p_predicate, p_object_id, p_user_id, p_fact, p_t_valid, v_bound);

    RETURN CASE WHEN v_count > 0 THEN 'supersede' ELSE 'insert' END;
END;
$$ LANGUAGE plpgsql;

-- R2 RETRACT: expire a row recorded in error (transaction axis only; keeps audit trail).
CREATE OR REPLACE FUNCTION bt_retract_edge(p_edge_id TEXT, p_expired_at TIMESTAMPTZ DEFAULT now())
RETURNS VOID AS $$
    UPDATE memory_edge SET expired_at = p_expired_at WHERE edge_id = p_edge_id AND expired_at IS NULL;
$$ LANGUAGE sql;

-- ---------------------------------------------------------------------
-- 3. Point-in-time query semantics (the predicates SM-G4/X11 consume).
-- ---------------------------------------------------------------------

-- 5a. "true at world-time :T" per current belief (returns one fact per identity).
--   SELECT * FROM memory_edge
--    WHERE user_id = :u AND subject_id = :s AND predicate = :p
--      AND t_valid <= :T
--      AND :T < COALESCE(t_invalid, 'infinity'::timestamptz)
--      AND expired_at IS NULL;

-- 5b. "as believed at transaction-time :TT, about world-time :T" (both axes; audit).
--   SELECT * FROM memory_edge
--    WHERE user_id = :u AND subject_id = :s AND predicate = :p
--      AND t_valid    <= :T  AND :T  < COALESCE(t_invalid,  'infinity'::timestamptz)
--      AND created_at <= :TT AND :TT < COALESCE(expired_at, 'infinity'::timestamptz);

-- Convenience view: the live graph (true now AND currently believed).
CREATE OR REPLACE VIEW memory_edge_live AS
    SELECT * FROM memory_edge WHERE expired_at IS NULL AND t_invalid IS NULL;

-- ---------------------------------------------------------------------
-- 4. Vector/episodic tier -- ADDITIVE migration (filter-only valid-time).
--    Decision (SM-R10): the graph is the bi-temporal system-of-record; the vector tier
--    carries valid_from/valid_to for point-in-time FILTERING only -- no invalidation
--    authority. EXTENDS the seed MEMORY_TABLE_SCHEMA (created_at/updated_at, epoch
--    BigInteger); does NOT fork UserMemory. Nullable => backward compatible.
-- ---------------------------------------------------------------------
--   ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_from BIGINT;  -- epoch s; NULL = unknown
--   ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_to   BIGINT;  -- epoch s; NULL = open
--   -- Recall filter (applied on top of vector similarity):
--   --   AND valid_from <= :T AND :T < COALESCE(valid_to, 9223372036854775807);
