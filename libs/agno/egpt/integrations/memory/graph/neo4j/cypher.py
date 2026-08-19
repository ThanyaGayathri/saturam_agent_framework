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

* ``(:Episode)-[:MENTIONS]->(:Entity)`` -- the episodic subgraph's link (SM-G6 #104). Not one of
  the three above: it is an index of what a live episode names, carrying no belief and no
  bi-temporal envelope of its own, so it is replaced with the episode rather than expired.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from egpt.memory.graph.episodic import (
    MENTIONS,
    require_recall_filters,
    require_recall_limit,
    require_time_window,
)
from egpt.memory.graph.semantic import require_fact_filters
from egpt.memory.types import SCOPE_SLOTS, Scope, require_scope

__all__ = [
    "ENTITY",
    "ENTITY_PROPERTIES",
    "EPISODE",
    "FACT",
    "MENTIONS",
    "REL",
    "SCHEMA_STATEMENTS",
    "ROW_PROPERTIES",
    "SCOPE_SLOTS",
    "bounds_statements",
    "clear_mentions_statement",
    "episode_probe_statement",
    "expire_statements",
    "insert_bounded_copy_statements",
    "link_mentions_statement",
    "live_row_statements",
    "recall_episodes_statement",
    "recall_facts_statement",
    "scope_predicate",
    "scope_props",
    "traverse_statements",
    "upsert_entities_statement",
    "upsert_episodes_statement",
    "upsert_facts_statements",
]

#: Labels. ``REL`` is a relationship type; the other three are node labels.
EPISODE = "Episode"
FACT = "Fact"
ENTITY = "Entity"
REL = "REL"


def scope_props(scope: Scope, *, method: str = "scope_props") -> Dict[str, Any]:
    """The scope columns to store on a row, plus the merge identity.

    ``scope_key`` (:meth:`Scope.key`) is the *write* identity -- fixed-arity and percent-escaped, so
    two scopes cannot collide. The individual slots are what *reads* filter on, because an unset
    slot means "any" and an equality on the composite key could not express that.

    Fail-closed (SM-C4 #92): see :func:`scope_predicate`, including why ``method`` is threaded.
    A caller reaching this directly on a write path gets its own name in the refusal rather than
    this function's.
    """
    require_scope(scope, method=method)
    return {
        "scope_key": scope.key(),
        **{slot: getattr(scope, slot) for slot in SCOPE_SLOTS},
    }


def scope_predicate(alias: str, scope: Scope, *, method: str = "scope_predicate") -> Tuple[str, Dict[str, Any]]:
    """The conjunctive scope filter for ``alias``, and the parameters it binds.

    Every slot the *call* scope sets becomes an equality; a slot left ``None`` means "any" and
    contributes no clause. ``tenant_id`` is mandatory on :class:`Scope`, so there is always at
    least one.

    **This is where the graph substrate fails closed (SM-C4 #92).** Every read predicate is built
    here and every stored row's scope columns come from :func:`scope_props`, so refusing an
    unscoped call at these two points means no Cypher can filter, and no row can be written,
    without a tenant. The two write-statement builders bind no scope at all and validate it
    directly, which closes the module: no public builder here accepts an unscoped call.

    Guarded at the data layer and not only at the seam because the ticket's Decision 1 rejects
    API-only scoping as bypassable -- and ``CoLocatedMemoryProvider.graph`` is a public attribute,
    so the seam's guard is genuinely not the last line.

    ``method`` is the builder the caller actually invoked, threaded down so the refusal names it.
    Six of the ten public builders reach this function through a private helper, and reporting
    ``scope_predicate`` for all of them sends a reader to a frame they never called -- the same
    misdirection the adapters avoid by guarding each entry point under its own verb. The default
    covers a direct call.
    """
    require_scope(scope, method=method)
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


def _scoped_live(alias: str, scope: Scope, method: str) -> Tuple[str, Dict[str, Any]]:
    """The two predicates that guard every read: in scope, and currently believed."""
    predicate, params = scope_predicate(alias, scope, method=method)
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

#: The same columns without ``score``, plus the episode's own scope slots and its mention names.
#: Spelled out rather than built from ``_RETURN`` by string surgery -- a ``.replace(", score", "")``
#: silently produces the wrong text the day ``_RETURN`` gains a second occurrence of that substring
#: or renames the column, and nothing would catch it.
#:
#: ``recall_episodes`` is ordered by time and not ranked, so a score column would be a null the
#: caller has to know to ignore -- and ``MemoryRecord.score`` left unset says that better than 0.0
#: does.
#:
#: The scope slots are the episode's **own write scope**, not the caller's -- and that distinction
#: is load-bearing here in a way it is not for ``traverse``: this is the only read whose
#: ``MemoryRecord`` carries a **derived** field (``entity_ids``) computed from ``scope``. Returning
#: the call scope under a tenant-wide read produced ids like ``acme/*/*/*/*/entity/berlin`` against a
#: node actually merged at ``acme/*/ada/*/*/entity/berlin`` -- entity ids that addressed nothing
#: (round-1 review, PR #90). ``_record`` reconstructs the write ``Scope`` from these columns rather
#: than parsing it back out of the ``MENTIONS`` edge's ``scope_key`` string, which has no inverse of
#: :func:`~egpt.memory.types.escape_key_slot` to parse it with.
_RECALL_RETURN = (
    "RETURN row.id AS id, row.tier AS tier, row.content AS content, row.topics AS topics, "
    "row.t_valid AS t_valid, row.t_invalid AS t_invalid, row.created_at AS created_at, "
    "row.expired_at AS expired_at, row.superseded_by AS superseded_by, "
    "row.provenance AS provenance, row.metadata AS metadata, "
    + ", ".join(f"row.{slot} AS {slot}" for slot in SCOPE_SLOTS)
    + ", mentions"
)


def _traverse(match: str, alias: str, scope: Scope, method: str) -> Tuple[str, Dict[str, Any]]:
    # The tier predicate belongs here, beside the scope and current-belief ones, because `LIMIT` is
    # applied by this statement: a tier filtered after the fact cuts rows the limit already spent.
    predicate, params = _scoped_live(alias, scope, method)
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
        _traverse(f"MATCH (e:{EPISODE})", "e", scope, "traverse_statements"),
        _traverse(f"MATCH (f:{FACT})", "f", scope, "traverse_statements"),
        _traverse(f"MATCH (:{ENTITY})-[r:{REL}]->(:{ENTITY})", "r", scope, "traverse_statements"),
    ]


_BOUNDS_RETURN = (
    "RETURN row.id AS id, row.t_valid AS t_valid, row.t_invalid AS t_invalid, "
    "row.expired_at AS expired_at, row.superseded_by AS superseded_by"
)


def _bounds(match: str, alias: str, scope: Scope, method: str) -> Tuple[str, Dict[str, Any]]:
    # Scoped but **not** filtered to current belief, and unranked and unlimited: the whole point is
    # to see the bound that ranking hid, and under SCD-2 that bound may sit on an expired revision.
    predicate, params = scope_predicate(alias, scope, method=method)
    return (f"{match} WHERE {predicate} AND {alias}.id IN $ids WITH {alias} AS row {_BOUNDS_RETURN}"), params


def bounds_statements(scope: Scope) -> List[Tuple[str, Dict[str, Any]]]:
    """Direct lookup by id across all three shapes -- the authority query, not a second read path."""
    return [
        _bounds(f"MATCH (e:{EPISODE})", "e", scope, "bounds_statements"),
        _bounds(f"MATCH (f:{FACT})", "f", scope, "bounds_statements"),
        _bounds(f"MATCH (:{ENTITY})-[r:{REL}]->(:{ENTITY})", "r", scope, "bounds_statements"),
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


# `scope` binds no parameter in either builder below, and is deliberately kept: a write carries its
# scope on each row (see `_WRITE_BINDS`), so there is nothing to bind -- but dropping the parameter
# would make these two the only statement builders a caller invokes differently, which is how the
# scope stops being passed at all. Since SM-C4 they validate it rather than ignore it, so the
# fail-closed property holds over *every* public builder here and not only the ones that filter.
def upsert_episodes_statement(scope: Scope) -> Tuple[str, Dict[str, Any]]:
    require_scope(scope, method="upsert_episodes_statement")
    return _upsert_node(EPISODE)


def clear_mentions_statement(scope: Scope) -> Tuple[str, Dict[str, Any]]:
    """Detach the live episode's current mention edges, before the new set is linked.

    Runs on the **episode** rows, so it needs no parameter of its own and cannot go looking at an
    episode the write did not touch. ``upsert_episodes`` replaces by id, so the edges have to be
    replaced with it: an event re-upserted with a shorter entity list would otherwise keep the ones
    it dropped, and read as mentioning an entity its current content does not name.

    A ``DELETE`` in an append-only store, which is worth naming. The append-only rule (SM-R10
    section 6.1) protects **belief** -- a bounded revision must stay reconstructible. A mention edge
    is not a belief: it is an index of what the live episode names, and the episode row it indexes
    was itself just overwritten by ``MERGE ... SET n = row``. Keeping the stale edge would preserve
    a link to content that no longer exists.
    """
    require_scope(scope, method="clear_mentions_statement")
    return (
        "UNWIND $rows AS row "
        f"MATCH (e:{EPISODE} {{scope_key: row.scope_key, id: row.id, {LIVE}: true}})"
        f"-[m:{MENTIONS}]->(:Entity) DELETE m"
    ), _WRITE_BINDS


def link_mentions_statement(scope: Scope) -> Tuple[str, Dict[str, Any]]:
    """Link each live episode to the entities it names, creating entity nodes as needed.

    ``MERGE`` on ``link_id`` rather than on the bare ``(e)-[:MENTIONS]->(n)`` pattern, for
    :func:`_upsert_node`'s reason: a relationship merge with both endpoints bound is a check-then-act
    race, and the uniqueness constraint on that key is what makes one-edge-per-(episode, entity) the
    database's guarantee. The key comes from ``egpt.memory.graph.episodic.mention_link_id``.

    ``MATCH`` on the episode, never ``MERGE``: this statement runs after the node write in the same
    transaction, so a miss means the episode is not there -- and conjuring an empty ``(:Episode)``
    to hang an edge off would put a contentless node into every read.

    Entities are merged on ``(scope_key, name)``, the same identity ``upsert_facts_statements``
    uses, so a mention and a fact about one entity land on one node rather than two.
    """
    require_scope(scope, method="link_mentions_statement")
    return (
        "UNWIND $mentions AS row "
        f"MATCH (e:{EPISODE} {{scope_key: row.scope_key, id: row.episode_id, {LIVE}: true}}) "
        "MERGE (n:Entity {scope_key: row.scope_key, name: row.name}) "
        "ON CREATE SET n += row.entity "
        f"MERGE (e)-[r:{MENTIONS} {{link_id: row.props.link_id}}]->(n) SET r = row.props"
    ), _WRITE_BINDS


def recall_episodes_statement(
    scope: Scope,
    *,
    entities: Optional[Sequence[str]] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Episodes by half-open valid-time window and by mention link, in the order they happened.

    Every filter is applied in Cypher rather than after the fact, for the reason
    :func:`_traverse` gives: ``LIMIT`` is applied server-side, so a predicate evaluated afterwards
    cuts rows the limit already spent and the short page looks like a genuine miss.

    ``since`` / ``until`` compare against ``t_valid``, which is the episode's ``occurred_at``. A
    ``NULL`` ``t_valid`` fails both comparisons and so is excluded by *either* bound -- which is
    correct (an episode with no known time is not in any window) but is the kind of three-valued
    behaviour worth stating rather than discovering.

    The mention filter is an ``EXISTS`` subquery, not a second ``MATCH``: matching the edge in the
    pattern would return one row per matching entity, and the ``LIMIT`` would then count edges
    rather than episodes.

    ``LIMIT`` is omitted from the text entirely when unbounded, because ``LIMIT $limit`` with a null
    parameter is an error rather than "no limit". ``limit=0`` keeps the clause and returns nothing,
    as :meth:`~egpt.memory.substrates.VectorSubstrate.scan` defines it.

    The mention edges are collected on the way out, ordered by the ``position`` the write recorded,
    so the names come back as the episode named them and match the row's ``entity_ids`` element for
    element rather than as a permutation.

    The defaults are all ``None`` so this builder is callable like every other one here, and the
    port's own guards run **inside** it rather than only in the substrate: an unfiltered
    ``MATCH (e:Episode)`` is the graph enumeration SM-G9 (#107) owns, and a builder that emitted one
    when called directly would be a way around the refusal. Scope is checked first, so an unscoped
    call is refused as unscoped rather than as unfiltered.
    """
    predicate, params = _scoped_live("e", scope, "recall_episodes_statement")
    require_recall_filters(entities, since, until)
    require_time_window(since, until)
    require_recall_limit(limit)
    if since is not None:
        predicate += " AND e.t_valid >= $since"
        params["since"] = since
    if until is not None:
        predicate += " AND e.t_valid < $until"
        params["until"] = until
    if entities is not None:
        predicate += f" AND EXISTS {{ MATCH (e)-[:{MENTIONS}]->(x:Entity) WHERE x.name IN $entities }}"
        params["entities"] = list(entities)
    text = (
        f"MATCH (e:{EPISODE}) WHERE {predicate} "
        f"OPTIONAL MATCH (e)-[men:{MENTIONS}]->(ment:Entity) "
        "WITH e, men, ment ORDER BY men.position ASC "
        "WITH e AS row, [n IN collect(ment.name) WHERE n IS NOT NULL] AS mentions "
        f"{_RECALL_RETURN} ORDER BY row.t_valid ASC, row.created_at ASC, row.content ASC, row.id ASC"
    )
    if limit is not None:
        text += " LIMIT $limit"
        params["limit"] = limit
    return text, params


#: The fact-edge columns :func:`recall_facts_statement` selects: the same envelope
#: ``_RECALL_RETURN`` uses, **plus** the edge's own scope slots -- for the reason
#: ``_RECALL_RETURN``'s own comment gives for episodes: under a tenant-wide call the edge's write
#: scope and the call scope disagree, and a caller reading ``record.scope`` afterwards needs the
#: former, not the latter (round-1 review, PR #102). Plus the three SPO fields read from the edge
#: and its endpoints rather than parsed out of ``content`` -- see ``GraphSubstrate.recall_facts``
#: for why that split matters.
_RECALL_FACTS_RETURN = (
    "RETURN row.id AS id, row.tier AS tier, row.content AS content, row.topics AS topics, "
    "row.t_valid AS t_valid, row.t_invalid AS t_invalid, row.created_at AS created_at, "
    "row.expired_at AS expired_at, row.superseded_by AS superseded_by, "
    "row.provenance AS provenance, row.metadata AS metadata, "
    + ", ".join(f"row.{slot} AS {slot}" for slot in SCOPE_SLOTS)
    + ", subject, row.predicate AS relation, object"
)


def recall_facts_statement(
    scope: Scope,
    *,
    entities: Optional[Sequence[str]] = None,
    relation: Optional[str] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Fact edges by entity, by relation label and by half-open valid-time window (SM-G7 #105).

    Only the ``(:Entity)-[r:REL]->(:Entity)`` shape -- the untripled ``(:Fact)`` node has no
    subject or object a filter here could match, matching
    :func:`~egpt.memory.graph.semantic.require_fact_filters`'s own scope.

    Every filter is applied in Cypher, not after the fact, for :func:`_traverse`'s reason: ``LIMIT``
    runs server-side, so a predicate evaluated afterwards cuts rows the limit already spent.

    The entity filter matches **either end** of the edge -- ``s.name`` or ``o.name`` -- because an
    anchor set is a disjunction over "involves this entity" the same way
    :func:`recall_episodes_statement`'s ``entities`` is a disjunction over "mentions this entity".
    ``relation`` compares against ``r.predicate`` exactly, with no case-folding: two spellings of one
    relation label are two labels, matching how two spellings of one entity are two nodes elsewhere
    on this backend -- folding either is a resolution policy, not a write-path decision.

    ``since`` / ``until`` compare against ``r.t_valid``, the same three-valued ``NULL`` behaviour
    :func:`recall_episodes_statement` documents.

    ``LIMIT`` is omitted from the text entirely when unbounded, matching
    :func:`recall_episodes_statement` -- ``LIMIT $limit`` with a null parameter is an error rather
    than "no limit".
    """
    predicate, params = _scoped_live("r", scope, "recall_facts_statement")
    require_fact_filters(entities, relation, since, until)
    require_time_window(since, until)
    require_recall_limit(limit)
    if since is not None:
        predicate += " AND r.t_valid >= $since"
        params["since"] = since
    if until is not None:
        predicate += " AND r.t_valid < $until"
        params["until"] = until
    if relation is not None:
        predicate += " AND r.predicate = $relation"
        params["relation"] = relation
    if entities is not None:
        predicate += " AND (s.name IN $entities OR o.name IN $entities)"
        params["entities"] = list(entities)
    text = (
        f"MATCH (s:{ENTITY})-[r:{REL}]->(o:{ENTITY}) WHERE {predicate} "
        "WITH r AS row, s.name AS subject, o.name AS object "
        f"{_RECALL_FACTS_RETURN} "
        "ORDER BY row.t_valid ASC, row.created_at ASC, row.content ASC, row.id ASC"
    )
    if limit is not None:
        text += " LIMIT $limit"
        params["limit"] = limit
    return text, params


def upsert_facts_statements(scope: Scope) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """The two fact shapes, keyed by which one a batch's rows belong to: a tripled fact is a
    relation between entities, an untripled one a node. Both carry the same envelope.

    **Returns a dict, unlike every other builder here -- iterate it with ``.items()``.** The natural
    ``for text, params in upsert_facts_statements(scope)`` unpacks the *keys* and fails with a bare
    ``ValueError``. A dict rather than a list because the caller has to know which batch each
    statement takes, and pairing them by position would be worse.

    Entities are merged on ``(scope_key, name)`` -- the **full** scope, not the tenant. So two users
    of one tenant each get their own ``(:Entity)`` node for the same name, and nothing is shared
    across a scope boundary. That is deliberate: resolving one user's "berlin" against another's is
    entity resolution, which is SM-G8's (#106), and a backend that pre-emptively shared them would
    have made the isolation guarantee depend on a dedup policy nobody has written yet.
    """
    require_scope(scope, method="upsert_facts_statements")
    triple = (
        "UNWIND $rows AS row "
        f"MERGE (s:{ENTITY} {{scope_key: row.scope_key, name: row.subject}}) "
        "ON CREATE SET s += row.entity "
        f"MERGE (o:{ENTITY} {{scope_key: row.scope_key, name: row.object}}) "
        "ON CREATE SET o += row.entity "
        "WITH row, s, o "
        # The same MERGE-not-check-then-act rule as `_upsert_node`, spelled again here rather than
        # shared: this leg binds two entity nodes the node leg has no notion of, and a relationship
        # MERGE is a different pattern. The invariant is shared by the constraint, not by the text.
        f"MERGE (s)-[r:{REL} {_IDENTITY}]->(o) SET r = row.props"
    )
    return {"triple": (triple, _WRITE_BINDS), "node": _upsert_node(FACT)}


#: The entity node's identity: the scope and the **surface** name, which is the same key the triple
#: leg above merges its endpoints on. Two spellings of one entity are two nodes; resolving them is
#: SM-G8's (#106), and a write path that merged on the normalised ``key`` instead would have decided
#: that policy here -- and would then disagree with every endpoint ``upsert_facts`` creates.
_ENTITY_IDENTITY = "{scope_key: row.scope_key, name: row.name}"

#: Every property an ``(:Entity)`` node can carry, for the same anti-drift reason as
#: :data:`ROW_PROPERTIES`. It is a **different** set: an entity is not bi-temporal (ADR 0003
#: section 1), so none of the four timestamps or the live marker appear here.
ENTITY_PROPERTIES = (
    "scope_key",
    "name",
    "key",
    "entity_type",
    "description",
    "properties",
    "aliases",
    "source_episode_ids",
    "embedding",
    "embedding_version",
    "confidence",
    "derived_by",
    "created_at",
    "updated_at",
    *SCOPE_SLOTS,
)


def _union_clause(name: str) -> str:
    """Append what the batch adds to what the node already has, without duplicating.

    ``coalesce`` covers the node an endpoint ``MERGE`` in :func:`upsert_facts_statements` created
    with no list at all, and the comprehension drops what the incoming batch already carries, so a
    re-extraction of the same episode is idempotent rather than accumulating copies.
    """
    return f"n.{name} = [x IN coalesce(n.{name}, []) WHERE NOT x IN row.{name}] + row.{name}"


def upsert_entities_statement(scope: Scope) -> Tuple[str, Dict[str, Any]]:
    """Merge extracted entity nodes, unioning provenance and aliases (SM-G2 #100).

    ``source_episode_ids`` and ``aliases`` accumulate where every other write on this backend
    replaces by id. Replacing them would make the second episode to mention an entity erase the
    first as its provenance, which is the fact -> episode audit trail SM-R7 section 7 makes
    load-bearing. Everything else is last-write: a description is a summary, not a running total.

    ``created_at`` is meant to mark the first sighting -- but ``coalesce``, not ``ON CREATE``,
    because *this* MERGE is not always the node's creation. ``upsert_facts_statements`` MERGEs the
    same identity as a triple endpoint and sets no ``created_at`` at all, so a node this method
    first meets already existing (SM-G3 wrote it first) has a live node with `created_at = null`.
    ``ON CREATE`` would then never fire again -- the field stays null forever, since nothing else
    sets it. ``coalesce`` fills a null unconditionally, and leaves an already-timestamped node
    alone, so first-sighting semantics hold across both write orders.

    **The three ``SET`` clauses' order is readable but not load-bearing** -- checked directly
    against the engine rather than assumed: reversing them produces an identical result, because
    ``created_at``, ``props``, and the two unioned fields are disjoint key sets that never write
    over one another. That disjointness is the actual invariant: ``props`` must never carry
    ``created_at``, ``source_episode_ids`` or ``aliases`` (``Neo4jGraphSubstrate._entity_rows`` keeps
    them out, and ``test_the_entity_row_builder_writes_nothing_outside_the_declared_properties`` pins
    it) -- that is what would make one clause's ``SET`` overwrite what another already wrote.
    """
    require_scope(scope, method="upsert_entities_statement")
    return (
        "UNWIND $rows AS row "
        f"MERGE (n:{ENTITY} {_ENTITY_IDENTITY}) "
        "SET n.created_at = coalesce(n.created_at, row.created_at) "
        "SET n += row.props "
        f"SET {_union_clause('source_episode_ids')}, {_union_clause('aliases')}"
    ), _WRITE_BINDS


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
        _read_live(f"MATCH (f:{FACT})", "f", scope, "live_row_statements"),
        _read_live(f"MATCH (:{ENTITY})-[r:{REL}]->(:{ENTITY})", "r", scope, "live_row_statements"),
    ]


def _read_live(match: str, alias: str, scope: Scope, method: str) -> Tuple[str, Dict[str, Any]]:
    predicate, params = _scoped_live(alias, scope, method)
    return (
        f"{match} WHERE {predicate} AND {alias}.id = $edge_id "
        f"RETURN {alias}.row_id AS row_id, {alias}.t_valid AS t_valid, {alias}.t_invalid AS t_invalid"
    ), params


def episode_probe_statement(scope: Scope) -> Tuple[str, Dict[str, Any]]:
    """The **live** episode with this id in this scope, or nothing.

    "Live" is load-bearing and the name does not carry it: an already-archived episode does not
    match, so ``invalidate`` treats it as absent and no-ops rather than refusing. That is the right
    terminal behaviour -- an archived row has already left current belief, and there is nothing to
    expire-and-reinsert -- but it is the kind of thing a reader assumes is an existence check.

    Asked before an invalidation, so the refusal names the right owner.

    ADR 0003 section B.6 gives episodic rows a different lifecycle from graph edges, so an
    ``invalidate`` aimed at one is a routing mistake rather than a no-op -- and a no-op here would
    look exactly like "the id is in another tenant".
    """
    predicate, params = _scoped_live("e", scope, "episode_probe_statement")
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
        _expire(f"MATCH (f:{FACT})", "f", scope, "expire_statements"),
        _expire(f"MATCH (:{ENTITY})-[r:{REL}]->(:{ENTITY})", "r", scope, "expire_statements"),
    ]


def _expire(match: str, alias: str, scope: Scope, method: str) -> Tuple[str, Dict[str, Any]]:
    predicate, params = _scoped_live(alias, scope, method)
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
        _bounded_copy(
            f"MATCH (f:{FACT})", "f", f"CREATE (m:{FACT}) SET m = f{overrides}", scope, "insert_bounded_copy_statements"
        ),
        _bounded_copy(
            f"MATCH (s:{ENTITY})-[r:{REL}]->(o:{ENTITY})",
            "r",
            f"CREATE (s)-[n:{REL}]->(o) SET n = r{overrides}",
            scope,
            "insert_bounded_copy_statements",
        ),
    ]


def _bounded_copy(match: str, alias: str, create: str, scope: Scope, method: str) -> Tuple[str, Dict[str, Any]]:
    # `expired_at = $at` as well as the row id: it pins the copy to the row **this call** expired.
    # Matching on `row_id` alone would let a second invalidation that lost the race to the expire
    # still copy the row, leaving two successors both claiming to be current belief.
    predicate, params = scope_predicate(alias, scope, method=method)
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

#: Every property a ``MENTIONS`` edge carries. ``link_id`` is its identity (see
#: :func:`link_mentions_statement`); ``position`` is the order the episode named the entity in, kept
#: so the subgraph returns the same list as ``EpisodicRecord.entity_ids`` rather than a permutation
#: of it. The scope slots are here for the reason every stored row carries them -- an edge written
#: under one scope must never be readable through another, whichever end a future query starts from.
MENTION_PROPERTIES = ("link_id", "episode_id", "name", "position", "created_at", "scope_key", *SCOPE_SLOTS)

#: A node created and deleted at schema init, purely to register the property-key tokens.
#:
#: Neo4j warns when a query names a property no row has ever set, which every read here does on a
#: fresh store -- ``superseded_by`` and ``topics`` are null until something sets them. The tempting
#: fix is to silence the ``UNRECOGNIZED`` notification class, but that is the same signal that
#: catches a **typo** in a property name, where the query silently returns null. Registering the
#: keys removes the false positives and leaves the true ones. Tokens survive the delete.
#: Probe values have to be list-typed for these, because a token registered against a scalar still
#: warns when a query indexes it as a list.
#:
#: Extend this when adding any new list-typed property to :data:`ROW_PROPERTIES`,
#: :data:`ENTITY_PROPERTIES`, or :data:`MENTION_PROPERTIES` -- an omitted one silently loses the
#: false-positive suppression the probe exists for, on that property alone.
_LIST_PROPERTIES = frozenset({"topics", "aliases", "source_episode_ids", "embedding"})

#: Same reason as :data:`_LIST_PROPERTIES`, for the one property stored as an integer: ``position``
#: on a ``MENTIONS`` edge (SM-G6 #104). ``''`` would still suppress ``UNRECOGNIZED``, but it
#: registers the wrong type, so a later query comparing ``position`` numerically would warn about a
#: type mismatch that is not real.
_INT_PROPERTIES = frozenset({"position"})


def _probe_value(name: str) -> str:
    if name in _LIST_PROPERTIES:
        return "[]"
    if name in _INT_PROPERTIES:
        return "0"
    return "true" if name == LIVE else "''"


#: All three row shapes, deduplicated in declaration order: an entity node shares ``scope_key``,
#: ``name``, ``created_at`` and the scope slots with a row, a mention edge shares ``episode_id`` and
#: ``scope_key``, and a repeated key is a Cypher syntax error rather than a duplicate.
_PROBE_PROPERTIES = tuple(dict.fromkeys((*ROW_PROPERTIES, *ENTITY_PROPERTIES, *MENTION_PROPERTIES)))

_PROBE = (
    "CREATE (p:_EgptSchemaProbe {"
    + ", ".join(f"{name}: {_probe_value(name)}" for name in _PROBE_PROPERTIES)
    + "}) DELETE p"
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
    f"CREATE CONSTRAINT egpt_{ENTITY.lower()}_identity IF NOT EXISTS FOR (n:{ENTITY}) "
    "REQUIRE (n.scope_key, n.name) IS UNIQUE",
    # The mention edge's identity. Same rule as every other MERGE key here: without a constraint,
    # `MERGE` on the pattern is a check-then-act race and one episode can end up naming one entity
    # twice.
    # Parenthesized (`(r.link_id)`) rather than `r.link_id IS UNIQUE` bare: SM-G2's derived
    # merge-key/constraint conformance check (test_neo4j_graph_statements.py) reads a constraint's
    # key from `REQUIRE (...)`, and only this constraint's MERGE key -- unlike the other bare
    # single-property ones, which are surrogate keys nothing ever MERGEs on -- needs to be found.
    f"CREATE CONSTRAINT egpt_mentions_link IF NOT EXISTS FOR ()-[r:{MENTIONS}]-() REQUIRE (r.link_id) IS UNIQUE",
    f"CREATE CONSTRAINT egpt_{EPISODE.lower()}_row_id IF NOT EXISTS FOR (n:{EPISODE}) REQUIRE n.row_id IS UNIQUE",
    f"CREATE CONSTRAINT egpt_{FACT.lower()}_row_id IF NOT EXISTS FOR (n:{FACT}) REQUIRE n.row_id IS UNIQUE",
    f"CREATE INDEX egpt_{EPISODE.lower()}_scope IF NOT EXISTS FOR (n:{EPISODE}) ON (n.tenant_id, n.id)",
    # Backs `recall_episodes_statement`'s `t_valid` window predicate. The id-equality index above
    # does not help it -- that statement never mentions `id` -- so within one tenant a time-window
    # read was a sequential scan over every episode (round-1 review, PR #90).
    f"CREATE INDEX egpt_{EPISODE.lower()}_t_valid IF NOT EXISTS FOR (n:{EPISODE}) ON (n.tenant_id, n.t_valid)",
    f"CREATE INDEX egpt_{FACT.lower()}_scope IF NOT EXISTS FOR (n:{FACT}) ON (n.tenant_id, n.id)",
    f"CREATE INDEX egpt_rel_scope IF NOT EXISTS FOR ()-[r:{REL}]-() ON (r.tenant_id, r.id)",
    # Backs `recall_facts_statement`'s `t_valid` window predicate, for the same reason
    # `egpt_episode_t_valid` backs `recall_episodes_statement`'s: the id-equality index above never
    # helps it, since that statement never mentions `id` -- within one tenant a time-window
    # `recall_facts` read was a sequential scan over every REL edge (round-1 review, PR #102).
    f"CREATE INDEX egpt_rel_t_valid IF NOT EXISTS FOR ()-[r:{REL}]-() ON (r.tenant_id, r.t_valid)",
    f"CREATE INDEX egpt_{ENTITY.lower()}_scope IF NOT EXISTS FOR (n:{ENTITY}) ON (n.scope_key, n.name)",
    # SM-G8's (#106) first pass groups exact-match candidates on the normalised `key`, which the
    # identity index above -- keyed on the surface `name` -- does not back; without this, that pass
    # is a full scope scan. Review finding on PR #89, round 2.
    f"CREATE INDEX egpt_{ENTITY.lower()}_key IF NOT EXISTS FOR (n:{ENTITY}) ON (n.scope_key, n.key)",
    # SM-G8's embedding match over this index; without it, matching one new candidate against a
    # scope's existing entities is a full scan of every `:Entity` node across every scope. Review
    # finding on PR #89, round 2.
    #
    # `768` is Mode A's dimension (ADR 0003 A.3, `bge-base-en-v1.5`), hardcoded here as the same v1
    # tradeoff `ddl/schema.sql` makes for `vector(768)` (ADR 0003 section B.4: "blessed for v1, with
    # a named follow-up" to make it a migration parameter) -- a mixed-dimension deployment needs a
    # second index under Mode B, which is that same follow-up's to make config-driven rather than a
    # reason to leave this one unindexed.
    f"CREATE VECTOR INDEX egpt_{ENTITY.lower()}_embedding IF NOT EXISTS FOR (n:{ENTITY}) ON (n.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}",
)
