// =====================================================================
// SM-R10 -- Bi-temporal edge model: Neo4j / Cypher (graph tier)
// Graph is the bi-temporal system-of-record. Timestamps use native datetime().
// Open intervals are null upper bounds, coalesced to a max sentinel in predicates.
// Feeds SM-G4 (edges) / SM-G5 (invalidation).
// =====================================================================

// ---------------------------------------------------------------------
// 1. Constraints / indexes
// ---------------------------------------------------------------------
CREATE CONSTRAINT memory_entity_id IF NOT EXISTS
  FOR (n:Entity) REQUIRE n.id IS UNIQUE;
CREATE INDEX memory_rel_identity IF NOT EXISTS
  FOR ()-[e:REL]-() ON (e.user_id, e.predicate);
CREATE INDEX memory_rel_valid IF NOT EXISTS
  FOR ()-[e:REL]-() ON (e.t_valid);

// ---------------------------------------------------------------------
// 2. Invalidate-not-delete upsert (R1 supersede + NOOP guard).
//    $t_valid is an ISO-8601 string / epoch; caller passes datetime($t_valid).
//    Transaction-time-versioned (SCD-2): a conflicting current fact whose window CONTAINS
//    $t_valid is expired (expired_at set) and re-created as a bounded copy -- edges are never
//    mutated in place, so 5a (current) and 5b (past belief) both reconstruct.
// ---------------------------------------------------------------------
// NOOP guard (run first; skip the rest if it returns a row):
//   MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity {id:$object})
//   WHERE e.expired_at IS NULL
//     AND e.t_valid <= datetime($t_valid)
//     AND datetime($t_valid) < coalesce(e.t_invalid, datetime('9999-12-31T00:00:00Z'))
//   RETURN e;
//
// R1 SUPERSEDE: expire each conflicting current fact whose window contains $t_valid and
// re-create it bounded (transaction-time versioning):
MATCH (s:Entity {id: $subject})-[e:REL {user_id: $user, predicate: $predicate}]->(o:Entity)
WHERE e.expired_at IS NULL
  AND o.id <> $object
  AND e.t_valid <= datetime($t_valid)
  AND datetime($t_valid) < coalesce(e.t_invalid, datetime('9999-12-31T00:00:00Z'))
SET e.expired_at = datetime()
WITH s, o, e
CREATE (s)-[:REL {
  edge_id: randomUUID(), user_id: e.user_id, predicate: e.predicate, fact: e.fact,
  t_valid: e.t_valid, t_invalid: datetime($t_valid),
  created_at: datetime(), expired_at: null
}]->(o);
// insert the new fact (t_invalid = earliest later conflicting fact's t_valid, or null):
MATCH (s:Entity {id: $subject}), (o:Entity {id: $object})
CREATE (s)-[:REL {
  edge_id: $edge_id, user_id: $user, predicate: $predicate, fact: $fact,
  t_valid: datetime($t_valid), t_invalid: $t_invalid_bound,
  created_at: datetime(), expired_at: null
}]->(o);

// R2 RETRACT (error correction; transaction axis only):
//   MATCH ()-[e:REL {edge_id:$edge_id}]->() WHERE e.expired_at IS NULL
//   SET e.expired_at = datetime();

// ---------------------------------------------------------------------
// 3. Point-in-time query semantics
// ---------------------------------------------------------------------
// 5a. "true at world-time $T" per current belief:
//   MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity)
//   WHERE e.t_valid <= datetime($T)
//     AND datetime($T) < coalesce(e.t_invalid, datetime('9999-12-31T00:00:00Z'))
//     AND e.expired_at IS NULL
//   RETURN o.id AS object, e;
//
// 5b. "as believed at transaction-time $TT, about world-time $T":
//   MATCH (s:Entity {id:$subject})-[e:REL {user_id:$user, predicate:$predicate}]->(o:Entity)
//   WHERE e.t_valid    <= datetime($T)  AND datetime($T)  < coalesce(e.t_invalid,  datetime('9999-12-31T00:00:00Z'))
//     AND e.created_at <= datetime($TT) AND datetime($TT) < coalesce(e.expired_at, datetime('9999-12-31T00:00:00Z'))
//   RETURN o.id AS object, e;
