"""The Cypher this backend can emit, and the fragments every statement is built from (SM-G1 #99).

Separate from the driver code so the statements are inspectable without a database. That is what
lets :mod:`tests.unit.memory.test_neo4j_graph_statements` assert the tenant predicate over **every**
statement rather than over the ones a test happened to exercise -- SM-R2 section 5.4 counted three
hand-written Cypher call sites where a forgotten ``user_id`` is a cross-tenant leak with no second
line of defence, and asked for a single seam so the predicate cannot be omitted by hand.

Row shapes, per ADR 0003 section 1 and SM-R12:

* ``(:Episode)`` -- one node per episode. ADR 0003 section 1 marks episodic-to-graph as
  "intended, not yet realised" and assigns the node type to this ticket.
* ``(:Fact)`` -- a semantic fact that carries no full triple. SPO is optional structure over the
  statement text, so a fact that resists extraction is still storable.
* ``(:Entity)-[:REL]->(:Entity)`` -- a semantic fact that does carry a full triple. The relation is
  the primary bi-temporal surface, and this is the shape SM-R10 measured 30/30 on.

All three carry the same four timestamps and the same scope columns, so the predicates below apply
to a node and a relationship alike.
"""

from typing import Any, Dict, List, Tuple

from egpt.memory.types import Scope

__all__ = [
    "EPISODE",
    "FACT",
    "REL",
    "SCHEMA_STATEMENTS",
    "ROW_PROPERTIES",
    "SCOPE_SLOTS",
    "bounds_statements",
    "episode_probe_statement",
    "expire_statements",
    "insert_bounded_copy_statements",
    "live_row_statements",
    "scope_predicate",
    "scope_props",
    "traverse_statements",
    "upsert_episodes_statement",
    "upsert_facts_statements",
]

#: Labels. ``REL`` is a relationship type; the other two are node labels.
EPISODE = "Episode"
FACT = "Fact"
REL = "REL"

#: The scope slots every row carries and every read filters on. ``tenant_id`` is not special-cased
#: anywhere below: a backend that honours the tenant and ignores the user serves one user's memory
#: to another inside the same tenant, which is the same leak one boundary in.
SCOPE_SLOTS = ("tenant_id", "agent_id", "user_id", "team_id", "session_id")


def scope_props(scope: Scope) -> Dict[str, Any]:
    """The scope columns to store on a row, plus the merge identity.

    ``scope_key`` (:meth:`Scope.key`) is the *write* identity -- fixed-arity and percent-escaped, so
    two scopes cannot collide. The individual slots are what *reads* filter on, because an unset
    slot means "any" and an equality on the composite key could not express that.
    """
    return {
        "scope_key": scope.key(),
        **{slot: getattr(scope, slot) for slot in SCOPE_SLOTS},
    }


def scope_predicate(alias: str, scope: Scope) -> Tuple[str, Dict[str, Any]]:
    """The conjunctive scope filter for ``alias``, and the parameters it binds.

    Every slot the *call* scope sets becomes an equality; a slot left ``None`` means "any" and
    contributes no clause. ``tenant_id`` is mandatory on :class:`Scope`, so there is always at
    least one.
    """
    clauses: List[str] = []
    params: Dict[str, Any] = {}
    for slot in SCOPE_SLOTS:
        value = getattr(scope, slot)
        if value is None:
            continue
        clauses.append(f"{alias}.{slot} = ${slot}")
        params[slot] = value
    return " AND ".join(clauses), params


#: Marks the currently-believed revision. Present exactly when ``expired_at IS NULL``, and paired
#: with it in one place -- ``expire_statements`` removes both together, and an integration test
#: asserts the two never disagree.
#:
#: It exists because a uniqueness constraint cannot express "unique among rows whose ``expired_at``
#: is null": Neo4j applies a composite constraint only to rows carrying **every** constrained
#: property, so a marker that expired rows simply lack gives exactly that partial index -- and with
#: it, a ``MERGE`` that is atomic rather than a check-then-act race.
LIVE = "live"


def _live(alias: str) -> str:
    """Current belief, on the transaction axis. Applied before ranking, or superseded rows eat the
    limit. Valid time is deliberately absent -- see ``GraphSubstrate.traverse`` for why it belongs
    to the merged set instead."""
    return f"{alias}.expired_at IS NULL"


def _scoped_live(alias: str, scope: Scope) -> Tuple[str, Dict[str, Any]]:
    """The two predicates that guard every read: in scope, and currently believed."""
    predicate, params = scope_predicate(alias, scope)
    return f"{predicate} AND {_live(alias)}", params


#: Jaccard overlap between the query terms and the row's content words, in ``[0.0, 1.0]`` as the
#: port requires. Computed server-side so ``limit`` cuts a ranked set rather than an arbitrary page,
#: and with no model call on the read path (issue #99's fourth criterion). Real traversal --
#: multi-hop from anchor entities -- is SM-G9's (#107); this ranks the rows the substrate holds.
_SCORE = (
    "WITH {alias}, [w IN split(toLower({alias}.content), ' ') WHERE w <> ''] AS ws "
    "WITH {alias}, [i IN range(0, size(ws) - 1) WHERE NOT ws[i] IN ws[0..i] | ws[i]] AS cw "
    "WITH {alias}, cw, size([t IN $terms WHERE t IN cw]) AS shared "
    "WITH {alias}, toFloat(shared) / (size($terms) + size(cw) - shared) AS score "
    "WHERE score > 0.0 "
)

#: Deterministic total order. Ties break on content before id, because ids are uuid4 and would
#: reorder identical content across runs (SM-C1 deviation D7).
_ORDER = "ORDER BY score DESC, row.created_at ASC, row.content ASC, row.id ASC LIMIT $limit"

_RETURN = (
    "RETURN row.id AS id, row.tier AS tier, row.content AS content, row.topics AS topics, "
    "row.t_valid AS t_valid, row.t_invalid AS t_invalid, row.created_at AS created_at, "
    "row.expired_at AS expired_at, row.superseded_by AS superseded_by, "
    "row.provenance AS provenance, row.metadata AS metadata, score"
)


def _traverse(match: str, alias: str, scope: Scope) -> Tuple[str, Dict[str, Any]]:
    # The tier predicate belongs here, beside the scope and current-belief ones, because `LIMIT` is
    # applied by this statement: a tier filtered after the fact cuts rows the limit already spent.
    predicate, params = _scoped_live(alias, scope)
    predicate = f"{predicate} AND {alias}.tier IN $tiers"
    return (
        f"{match} WHERE {predicate} "
        + _SCORE.format(alias=alias)
        + f"WITH {alias} AS row, score "
        + f"{_RETURN} {_ORDER}"
    ), params


def traverse_statements(scope: Scope) -> List[Tuple[str, Dict[str, Any]]]:
    """One statement per row shape. The caller merges, re-ranks, and applies ``limit`` again.

    Three statements rather than one ``UNION``: a union of three ranked, limited legs still needs a
    merge above it, so it buys nothing and costs the ability to read each statement on its own.

    **Cost model, stated because no index changes it.** The scoring runs per row over everything
    that survives the scope, tier and current-belief filters, before ``ORDER BY ... LIMIT``. None of
    the shipped indexes lead with a column this statement filters on, so the work scales with
    rows-in-scope rather than with ``limit`` -- a tenant-prefix scan at best, however narrow the
    caller's ``user_id`` happens to be. Picking an index for it is guesswork until someone measures
    at the real envelope, and SM-G9 (#107) replaces this read path outright; sizing it is the same
    unmeasured question as the graph-side isolation benchmark SM-R2 section 5.4 asks for.
    """
    return [
        _traverse(f"MATCH (e:{EPISODE})", "e", scope),
        _traverse(f"MATCH (f:{FACT})", "f", scope),
        _traverse(f"MATCH (:Entity)-[r:{REL}]->(:Entity)", "r", scope),
    ]


_BOUNDS_RETURN = (
    "RETURN row.id AS id, row.t_valid AS t_valid, row.t_invalid AS t_invalid, "
    "row.expired_at AS expired_at, row.superseded_by AS superseded_by"
)


def _bounds(match: str, alias: str, scope: Scope) -> Tuple[str, Dict[str, Any]]:
    # Scoped but **not** filtered to current belief, and unranked and unlimited: the whole point is
    # to see the bound that ranking hid, and under SCD-2 that bound may sit on an expired revision.
    predicate, params = scope_predicate(alias, scope)
    return (f"{match} WHERE {predicate} AND {alias}.id IN $ids WITH {alias} AS row {_BOUNDS_RETURN}"), params


def bounds_statements(scope: Scope) -> List[Tuple[str, Dict[str, Any]]]:
    """Direct lookup by id across all three shapes -- the authority query, not a second read path."""
    return [
        _bounds(f"MATCH (e:{EPISODE})", "e", scope),
        _bounds(f"MATCH (f:{FACT})", "f", scope),
        _bounds(f"MATCH (:Entity)-[r:{REL}]->(:Entity)", "r", scope),
    ]


#: The live row's identity: the full scope, the logical id, and the live marker.
#:
#: ``scope_key`` rather than the five slots because a ``MERGE`` key may not be null and four of the
#: slots are optional. It is injective over all five (:meth:`Scope.key`), so this is an exact-scope
#: match -- which is what a *write* wants, where a read's "unset means any" would be wrong.
_IDENTITY = "{scope_key: row.scope_key, id: row.id, " + LIVE + ": true}"

#: A write binds no scope parameters: the scope travels **on each row**, so one statement can
#: carry a batch and every row lands under the scope it was built with. The read statements
#: bind the slots instead, because a read's unset slot means "any" and a row property cannot.
_WRITE_BINDS: Dict[str, Any] = {}


def _upsert_node(label: str) -> Tuple[str, Dict[str, Any]]:
    """Replace the live row with this id, or create one. Expired revisions are never touched.

    ``MERGE``, not ``OPTIONAL MATCH`` then a conditional ``CREATE``. The latter is a check-then-act
    race with nothing between the read and the write: two rows of one ``UNWIND`` sharing an id both
    observed "no live row" and both created one, which is two live rows for one logical id --
    reproducible with a single caller and no concurrency at all. ``MERGE`` locks the pattern before
    deciding, and the uniqueness constraint on the same key makes the guarantee the database's
    rather than this statement's.

    ``SET n = row`` is the blanket overwrite SM-R10 section 6.1 names as a footgun -- but only on the
    bi-temporal axis, where it rewrites history in place. Here the merge key's live marker confines
    it to the one currently-believed row, which is the by-id replacement that makes a
    ``PartialWriteError`` retry idempotent.
    """
    return (f"UNWIND $rows AS row MERGE (n:{label} {_IDENTITY}) SET n = row"), _WRITE_BINDS


# `scope` is unused by both builders below, and deliberately kept: a write carries its scope on each
# row (see `_WRITE_BINDS`), so there is nothing to bind -- but dropping the parameter would make
# these two the only statement builders a caller invokes differently, which is how the scope stops
# being passed at all. The `noqa` says "unused"; this says "and that is the point".
def upsert_episodes_statement(scope: Scope) -> Tuple[str, Dict[str, Any]]:  # noqa: ARG001 - see above
    return _upsert_node(EPISODE)


def upsert_facts_statements(scope: Scope) -> Dict[str, Tuple[str, Dict[str, Any]]]:  # noqa: ARG001 - as above
    """The two fact shapes, keyed by which one a batch's rows belong to: a tripled fact is a
    relation between entities, an untripled one a node. Both carry the same envelope."""
    triple = (
        "UNWIND $rows AS row "
        "MERGE (s:Entity {scope_key: row.scope_key, name: row.subject}) "
        "ON CREATE SET s += row.entity "
        "MERGE (o:Entity {scope_key: row.scope_key, name: row.object}) "
        "ON CREATE SET o += row.entity "
        "WITH row, s, o "
        # The same MERGE-not-check-then-act rule as `_upsert_node`, spelled again here rather than
        # shared: this leg binds two entity nodes the node leg has no notion of, and a relationship
        # MERGE is a different pattern. The invariant is shared by the constraint, not by the text.
        f"MERGE (s)-[r:{REL} {_IDENTITY}]->(o) SET r = row.props"
    )
    return {"triple": (triple, _WRITE_BINDS), "node": _upsert_node(FACT)}


def live_row_statements(scope: Scope) -> List[Tuple[str, Dict[str, Any]]]:
    """The live row for one id, per **fact** shape -- read before an invalidation.

    Episodes are deliberately absent. ADR 0003 section B.6 rules that semantic contradiction on a
    graph **edge** goes through this SCD-2 primitive, while episodic **row** lifecycle -- SM-R6
    re-segmentation and SM-R5 decay archival -- is row-level ``expired_at`` on the record. They are
    append-only either way; they are different objects, and routing one through the other is what
    the port's "do not route episode archival here" forbids.

    ``invalidate`` needs the row's ``t_valid`` to refuse a bound that would leave the fact valid at
    no instant, and its ``t_invalid`` to keep the tighter of the two.
    """
    return [
        _read_live(f"MATCH (f:{FACT})", "f", scope),
        _read_live(f"MATCH (:Entity)-[r:{REL}]->(:Entity)", "r", scope),
    ]


def _read_live(match: str, alias: str, scope: Scope) -> Tuple[str, Dict[str, Any]]:
    predicate, params = _scoped_live(alias, scope)
    return (
        f"{match} WHERE {predicate} AND {alias}.id = $edge_id "
        f"RETURN {alias}.row_id AS row_id, {alias}.t_valid AS t_valid, {alias}.t_invalid AS t_invalid"
    ), params


def episode_probe_statement(scope: Scope) -> Tuple[str, Dict[str, Any]]:
    """Is this id an episode? Asked before an invalidation, so the refusal names the right owner.

    ADR 0003 section B.6 gives episodic rows a different lifecycle from graph edges, so an
    ``invalidate`` aimed at one is a routing mistake rather than a no-op -- and a no-op here would
    look exactly like "the id is in another tenant".
    """
    predicate, params = _scoped_live("e", scope)
    return (f"MATCH (e:{EPISODE}) WHERE {predicate} AND e.id = $edge_id RETURN e.id AS id LIMIT 1"), params


def expire_statements(scope: Scope) -> List[Tuple[str, Dict[str, Any]]]:
    """Close one row on the **transaction** axis. Never a valid-time edit, never a delete.

    Matched by ``row_id``, the surrogate key, rather than by ``(id, expired_at)``: two invalidations
    sharing an ``at`` -- which fixed test timestamps produce readily -- would otherwise leave two
    rows indistinguishable, and the reinsert below would copy both.

    One statement per shape rather than a chain: a non-optional ``MATCH`` that finds nothing ends
    the pipeline, so chaining them would make the third shape reachable only when the second
    matched -- which is exactly never, since one id lives in one shape.
    """
    return [
        _expire(f"MATCH (f:{FACT})", "f", scope),
        _expire(f"MATCH (:Entity)-[r:{REL}]->(:Entity)", "r", scope),
    ]


def _expire(match: str, alias: str, scope: Scope) -> Tuple[str, Dict[str, Any]]:
    predicate, params = _scoped_live(alias, scope)
    return (
        f"{match} WHERE {predicate} AND {alias}.row_id = $live_row_id "
        f"SET {alias}.expired_at = $at REMOVE {alias}.{LIVE}"
    ), params


def insert_bounded_copy_statements(scope: Scope) -> List[Tuple[str, Dict[str, Any]]]:
    """Reinsert the expired row carrying its new bound -- the other half of SCD-2.

    The copy keeps ``id``. Textbook SCD-2 gives the successor a fresh surrogate key, but the only
    thing carrying this bound onto the vector leg's stale projection is the merge's dedup **by id**,
    so a new id means the two never meet and the invalidated fact reads as current.

    The just-expired row is matched by its ``row_id``, not by current belief: it is no longer
    current, which is precisely what :func:`expire_statements` did to it.
    """
    # A map projection rather than `SET m = properties(x)` followed by overrides: Cypher applies set
    # items left to right and checks the uniqueness constraint after each one, so copying the
    # properties wholesale duplicates `row_id` for an instant and trips the constraint before the
    # override lands. The projection carries the new values into the single assignment.
    # `live: true` is restored on the successor -- the row this call just expired had it removed, and
    # a map projection copies what is there. Without it the successor is invisible to every read and
    # exempt from the uniqueness constraint, which is the guarantee this whole shape rests on.
    overrides = "{.*, row_id: $row_id, t_invalid: $t_invalid, expired_at: null, created_at: $at, " + LIVE + ": true}"
    return [
        _bounded_copy(f"MATCH (f:{FACT})", "f", f"CREATE (m:{FACT}) SET m = f{overrides}", scope),
        _bounded_copy(
            f"MATCH (s:Entity)-[r:{REL}]->(o:Entity)",
            "r",
            f"CREATE (s)-[n:{REL}]->(o) SET n = r{overrides}",
            scope,
        ),
    ]


def _bounded_copy(match: str, alias: str, create: str, scope: Scope) -> Tuple[str, Dict[str, Any]]:
    # `expired_at = $at` as well as the row id: it pins the copy to the row **this call** expired.
    # Matching on `row_id` alone would let a second invalidation that lost the race to the expire
    # still copy the row, leaving two successors both claiming to be current belief.
    predicate, params = scope_predicate(alias, scope)
    return (
        f"{match} WHERE {predicate} AND {alias}.row_id = $live_row_id AND {alias}.expired_at = $at {create}"
    ), params


#: Every property a stored row can carry. Kept here so the probe below cannot drift from the rows
#: themselves; ``test_neo4j_graph_statements`` asserts the row builder writes nothing outside it.
ROW_PROPERTIES = (
    "row_id",
    "id",
    "tier",
    "content",
    "topics",
    "t_valid",
    "t_invalid",
    "created_at",
    "expired_at",
    "superseded_by",
    "provenance",
    "metadata",
    "predicate",
    "scope_key",
    LIVE,
    *SCOPE_SLOTS,
)

#: A node created and deleted at schema init, purely to register the property-key tokens.
#:
#: Neo4j warns when a query names a property no row has ever set, which every read here does on a
#: fresh store -- ``superseded_by`` and ``topics`` are null until something sets them. The tempting
#: fix is to silence the ``UNRECOGNIZED`` notification class, but that is the same signal that
#: catches a **typo** in a property name, where the query silently returns null. Registering the
#: keys removes the false positives and leaves the true ones. Tokens survive the delete.
_PROBE = (
    "CREATE (p:_EgptSchemaProbe {"
    + ", ".join(
        f"{name}: []" if name == "topics" else f"{name}: true" if name == LIVE else f"{name}: ''"
        for name in ROW_PROPERTIES
    )
    + ", name: ''}) DELETE p"
)

#: Idempotent schema. Uniqueness is on ``row_id`` and not on ``id``: under SCD-2 one logical id has
#: many revisions, so a constraint on ``id`` would reject the bounded copy that invalidation exists
#: to insert.
#:
#: The indexes below back the **id-equality** reads -- ``bounds_for_ids``, ``live_row``, and the
#: entity merge. They do **not** help ``traverse``, which filters on the scope slots, ``tier`` and
#: ``expired_at`` and never mentions ``id``: see that statement's cost note in
#: :func:`traverse_statements`. Claiming otherwise here would be the more comfortable comment and
#: the false one.
#: The identity every write ``MERGE``s on. A composite uniqueness constraint here is what turns
#: ``MERGE`` from "usually one row" into a guarantee: Neo4j documents ``MERGE`` as safe under
#: concurrency only when a constraint backs the pattern. Expired revisions carry no ``live`` marker,
#: so the constraint does not apply to them and SCD-2 history accumulates freely.
_UNIQUE_LIVE = f"(n.scope_key, n.id, n.{LIVE}) IS UNIQUE"

SCHEMA_STATEMENTS = (
    _PROBE,
    f"CREATE CONSTRAINT egpt_{EPISODE.lower()}_live IF NOT EXISTS FOR (n:{EPISODE}) REQUIRE {_UNIQUE_LIVE}",
    f"CREATE CONSTRAINT egpt_{FACT.lower()}_live IF NOT EXISTS FOR (n:{FACT}) REQUIRE {_UNIQUE_LIVE}",
    # Relationship uniqueness, verified available on Community 5. Without it the `REL` shape -- this
    # module's "primary bi-temporal surface" -- would have strictly weaker protection than the nodes.
    f"CREATE CONSTRAINT egpt_rel_live IF NOT EXISTS FOR ()-[r:{REL}]-() "
    f"REQUIRE (r.scope_key, r.id, r.{LIVE}) IS UNIQUE",
    f"CREATE CONSTRAINT egpt_rel_row_id IF NOT EXISTS FOR ()-[r:{REL}]-() REQUIRE r.row_id IS UNIQUE",
    # Entities are MERGEd by (scope_key, name) on every triple write, so they need the same backing.
    "CREATE CONSTRAINT egpt_entity_identity IF NOT EXISTS FOR (n:Entity) REQUIRE (n.scope_key, n.name) IS UNIQUE",
    f"CREATE CONSTRAINT egpt_{EPISODE.lower()}_row_id IF NOT EXISTS FOR (n:{EPISODE}) REQUIRE n.row_id IS UNIQUE",
    f"CREATE CONSTRAINT egpt_{FACT.lower()}_row_id IF NOT EXISTS FOR (n:{FACT}) REQUIRE n.row_id IS UNIQUE",
    f"CREATE INDEX egpt_{EPISODE.lower()}_scope IF NOT EXISTS FOR (n:{EPISODE}) ON (n.tenant_id, n.id)",
    f"CREATE INDEX egpt_{FACT.lower()}_scope IF NOT EXISTS FOR (n:{FACT}) ON (n.tenant_id, n.id)",
    f"CREATE INDEX egpt_rel_scope IF NOT EXISTS FOR ()-[r:{REL}]-() ON (r.tenant_id, r.id)",
    "CREATE INDEX egpt_entity_scope IF NOT EXISTS FOR (n:Entity) ON (n.scope_key, n.name)",
)
