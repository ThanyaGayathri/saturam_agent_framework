-- =============================================================================
-- SM-R12 memory data model -- physical schema (PostgreSQL + pgvector)
-- -----------------------------------------------------------------------------
-- Mirrors benchmarks/memory_datamodel/model.py 1:1. SM-C3 promotes this into core
-- migrations. Conventions:
--   * Timestamps are BIGINT Unix epoch SECONDS (matches seed UserMemory/KnowledgeRow columns).
--   * Multi-tenancy = DECLARATIVE PARTITIONING BY LIST (tenant_id) per SM-R3 (#78): one
--     partition (=> its own smaller HNSW index) per tenant; shared-table tenant filter is
--     REJECTED by SM-R3's data. PK includes tenant_id (required by pg for partitioned tables).
--   * Embedding dim = 768 (SM-R4 Mode A default). Mode B: swap vector(768) -> vector(1024).
--     Storage pressure (>10M): swap vector -> halfvec to halve footprint (SM-R4 section 4).
--   * Bi-temporal = SM-R10 (#85) four-timestamp: created_at (tx-in), expired_at (tx-out),
--     t_valid / t_invalid (world validity). Invalidate-not-delete.
--   * Cross-table references (source_episode_ids, entity ids, links) are LOGICAL (no FK):
--     pg cannot cheaply FK across partitioned tables, and tenancy isolation is the boundary.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- -----------------------------------------------------------------------------
-- Tier: episodic -- raw events ("something that happened"). Embedded + decay + bi-temporal.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_episodic (
    tenant_id          TEXT        NOT NULL,                 -- SM-R3 partition key
    id                 TEXT        NOT NULL,
    user_id            TEXT,
    agent_id           TEXT,
    team_id            TEXT,
    session_id         TEXT,
    content            TEXT        NOT NULL,                 -- raw event text
    -- EpisodicContext (when/where/why) flattened:
    occurred_at        BIGINT,                               -- WHEN the event happened (epoch s)
    location           TEXT,                                 -- WHERE
    cause              TEXT,                                 -- WHY
    -- embedding (SM-R4):
    embedding          vector(768),
    embedding_dim      INTEGER,
    embedding_version  TEXT,
    -- decay (SM-R5): read-time importance-weighted recency; `strength` DEFERRED by SM-R5 (sec 5).
    importance         REAL        NOT NULL DEFAULT 0.5,
    last_accessed_at   BIGINT,
    -- bi-temporal (SM-R10):
    created_at         BIGINT      NOT NULL,                 -- tx time in
    updated_at         BIGINT,
    t_valid            BIGINT,
    t_invalid          BIGINT,
    expired_at         BIGINT,                               -- tx time out; NULL => current row
    -- graph links:
    entity_ids         TEXT[]      NOT NULL DEFAULT '{}',
    graph_node_id      TEXT,
    -- episode identity / segmentation (SM-R6, sec 4/5):
    revision           INTEGER     NOT NULL DEFAULT 0,       -- 0 inline; ++ on async re-cut
    supersedes         TEXT[]      NOT NULL DEFAULT '{}',    -- episode ids this re-cut replaced
    superseded_by      TEXT[]      NOT NULL DEFAULT '{}',    -- uuid[] identity graph (arrays: merge/split)
    boundary_method    TEXT,                                 -- time_gap|turn_cap|embedding_drift|em_llm|llm|manual
    boundary_trigger   DOUBLE PRECISION,                     -- idle-gap s / turns / sigma / model conf
    boundary_confidence REAL,                                -- [0,1]; 1.0 for deterministic rules
    refined            BOOLEAN     NOT NULL DEFAULT FALSE,   -- async lane re-cut this boundary?
    segmenter_version  TEXT,                                 -- reproducibility / re-segmentation
    metadata           JSONB       NOT NULL DEFAULT '{}',
    CONSTRAINT memory_episodic_tvalid_order CHECK (t_valid IS NULL OR t_invalid IS NULL OR t_invalid >= t_valid),
    CONSTRAINT memory_episodic_expiry_order CHECK (expired_at IS NULL OR expired_at >= created_at),
    PRIMARY KEY (tenant_id, id)
) PARTITION BY LIST (tenant_id);

-- -----------------------------------------------------------------------------
-- Tier: semantic / fact -- distilled durable facts (SPO) with provenance + bi-temporal.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_semantic_fact (
    tenant_id          TEXT        NOT NULL,
    id                 TEXT        NOT NULL,
    user_id            TEXT,
    agent_id           TEXT,
    team_id            TEXT,
    session_id         TEXT,
    content            TEXT        NOT NULL,                 -- the statement
    subject            TEXT,
    predicate          TEXT,
    object             TEXT,
    confidence         REAL        NOT NULL DEFAULT 1.0,
    source_episode_ids TEXT[]      NOT NULL DEFAULT '{}',    -- provenance: fact -> episodes
    derived_by         TEXT,                                 -- strategy/pass that produced it (SM-R7)
    entity_ids         TEXT[]      NOT NULL DEFAULT '{}',
    embedding          vector(768),
    embedding_dim      INTEGER,
    embedding_version  TEXT,
    importance         REAL        NOT NULL DEFAULT 0.5,     -- decay (SM-R5); `strength` deferred
    last_accessed_at   BIGINT,
    created_at         BIGINT      NOT NULL,
    updated_at         BIGINT,
    t_valid            BIGINT,
    t_invalid          BIGINT,
    expired_at         BIGINT,
    superseded_by      TEXT,
    metadata           JSONB       NOT NULL DEFAULT '{}',
    CONSTRAINT memory_semantic_fact_tvalid_order CHECK (t_valid IS NULL OR t_invalid IS NULL OR t_invalid >= t_valid),
    CONSTRAINT memory_semantic_fact_expiry_order CHECK (expired_at IS NULL OR expired_at >= created_at),
    PRIMARY KEY (tenant_id, id)
) PARTITION BY LIST (tenant_id);

-- -----------------------------------------------------------------------------
-- Tier: semantic / entity -- graph NODE. Embeddable (name/description), not bi-temporal.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_entity (
    tenant_id          TEXT        NOT NULL,
    id                 TEXT        NOT NULL,
    entity_type        TEXT        NOT NULL,                 -- company | person | project | ...
    name               TEXT,
    description        TEXT,
    properties         JSONB       NOT NULL DEFAULT '{}',
    user_id            TEXT,
    agent_id           TEXT,
    team_id            TEXT,
    session_id         TEXT,
    embedding          vector(768),
    embedding_dim      INTEGER,
    embedding_version  TEXT,
    created_at         BIGINT      NOT NULL,
    updated_at         BIGINT,
    metadata           JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, id)
) PARTITION BY LIST (tenant_id);

-- -----------------------------------------------------------------------------
-- Tier: semantic / relation -- graph EDGE. The primary SM-R10 bi-temporal surface.
-- Contradiction => set t_invalid (+ expired_at on correction); never DELETE.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_relation (
    tenant_id          TEXT        NOT NULL,
    id                 TEXT        NOT NULL,
    subject_entity_id  TEXT        NOT NULL,
    predicate          TEXT        NOT NULL,                 -- works_at | ceo_of | ...
    object_entity_id   TEXT        NOT NULL,
    confidence         REAL        NOT NULL DEFAULT 1.0,
    source_episode_ids TEXT[]      NOT NULL DEFAULT '{}',
    derived_by         TEXT,                                 -- strategy/pass that produced it (SM-R7)
    user_id            TEXT,
    agent_id           TEXT,
    team_id            TEXT,
    session_id         TEXT,
    created_at         BIGINT      NOT NULL,                 -- tx in
    updated_at         BIGINT,
    t_valid            BIGINT,                               -- world-valid from
    t_invalid          BIGINT,                               -- world-valid to (NULL => current)
    expired_at         BIGINT,                               -- tx out (NULL => current row)
    superseded_by      TEXT,
    metadata           JSONB       NOT NULL DEFAULT '{}',
    CONSTRAINT memory_relation_tvalid_order CHECK (t_valid IS NULL OR t_invalid IS NULL OR t_invalid >= t_valid),
    CONSTRAINT memory_relation_expiry_order CHECK (expired_at IS NULL OR expired_at >= created_at),
    PRIMARY KEY (tenant_id, id)
) PARTITION BY LIST (tenant_id);

-- -----------------------------------------------------------------------------
-- Tier: procedural -- learned rule / skill / prompt fragment. Embeddable, versioned via links.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_procedural (
    tenant_id          TEXT        NOT NULL,
    id                 TEXT        NOT NULL,
    kind               TEXT        NOT NULL DEFAULT 'rule',  -- rule | skill | prompt_fragment
    title              TEXT,
    content            TEXT        NOT NULL,                 -- the rule/skill/prompt body
    trigger            TEXT,                                 -- when/where it applies
    tags               TEXT[],
    success_count      INTEGER,
    user_id            TEXT,
    agent_id           TEXT,
    team_id            TEXT,
    session_id         TEXT,
    embedding          vector(768),
    embedding_dim      INTEGER,
    embedding_version  TEXT,
    created_at         BIGINT      NOT NULL,
    updated_at         BIGINT,
    metadata           JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, id)
) PARTITION BY LIST (tenant_id);

-- -----------------------------------------------------------------------------
-- Links -- typed cross-tier edge (episode->entity, fact->episode, record->supersedes). Bi-temporal.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_link (
    tenant_id          TEXT        NOT NULL,
    id                 TEXT        NOT NULL,
    from_id            TEXT        NOT NULL,
    from_tier          TEXT        NOT NULL,                 -- working|episodic|semantic|procedural
    to_id              TEXT        NOT NULL,
    to_tier            TEXT        NOT NULL,
    link_type          TEXT        NOT NULL DEFAULT 'relates_to',
    user_id            TEXT,
    agent_id           TEXT,
    team_id            TEXT,
    session_id         TEXT,
    created_at         BIGINT      NOT NULL,
    updated_at         BIGINT,
    t_valid            BIGINT,
    t_invalid          BIGINT,
    expired_at         BIGINT,
    superseded_by      TEXT,
    metadata           JSONB       NOT NULL DEFAULT '{}',
    CONSTRAINT memory_link_tvalid_order CHECK (t_valid IS NULL OR t_invalid IS NULL OR t_invalid >= t_valid),
    CONSTRAINT memory_link_expiry_order CHECK (expired_at IS NULL OR expired_at >= created_at),
    PRIMARY KEY (tenant_id, id)
) PARTITION BY LIST (tenant_id);

-- Working memory (CoALA working tier) is transient/session-scoped: it lives in the existing
-- session store, NOT a durable memory table (no embedding, no bi-temporal). Intentionally no DDL.

-- =============================================================================
-- Per-tenant partitions + indexes.
-- SM-C3 runs this block per tenant at onboarding (templated). Example: tenant 'acme'.
-- Each partition carries its own HNSW index => SM-R3's "smaller per-tenant index" that keeps
-- recall high as tenant count grows. HNSW uses cosine ops (SM-R4 normalises to cosine).
-- =============================================================================
-- CREATE TABLE memory_episodic_acme        PARTITION OF memory_episodic        FOR VALUES IN ('acme');
-- CREATE TABLE memory_semantic_fact_acme   PARTITION OF memory_semantic_fact   FOR VALUES IN ('acme');
-- CREATE TABLE memory_entity_acme          PARTITION OF memory_entity          FOR VALUES IN ('acme');
-- CREATE TABLE memory_relation_acme        PARTITION OF memory_relation        FOR VALUES IN ('acme');
-- CREATE TABLE memory_procedural_acme      PARTITION OF memory_procedural      FOR VALUES IN ('acme');
-- CREATE TABLE memory_link_acme            PARTITION OF memory_link            FOR VALUES IN ('acme');
--
-- Per-partition indexes (repeat per tenant partition):
-- HNSW vector indexes (build ef/ m tuned per SM-R3; ef_search >= k at query time):
-- CREATE INDEX ON memory_episodic_acme      USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX ON memory_semantic_fact_acme USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX ON memory_entity_acme        USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX ON memory_procedural_acme    USING hnsw (embedding vector_cosine_ops);
-- Bi-temporal / traversal btree indexes:
-- CREATE INDEX ON memory_episodic_acme      (t_valid, t_invalid);
-- CREATE INDEX ON memory_semantic_fact_acme (t_valid, t_invalid) WHERE expired_at IS NULL;
-- CREATE INDEX ON memory_relation_acme      (subject_entity_id);
-- CREATE INDEX ON memory_relation_acme      (object_entity_id);
-- CREATE INDEX ON memory_relation_acme      (t_valid, t_invalid) WHERE expired_at IS NULL;
-- CREATE INDEX ON memory_link_acme          (from_id);
-- CREATE INDEX ON memory_link_acme          (to_id);
