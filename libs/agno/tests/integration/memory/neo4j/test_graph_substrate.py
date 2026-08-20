"""The Neo4j graph substrate against a real engine (SM-G1 #99).

Gated on ``EGPT_INTEGRATION_NEO4J``; start the engine with ``cookbook/scripts/run_neo4j.sh``.

**This is the half SM-C1 could not write.** Its five acceptance criteria were an interface, and it
recorded that anything built there could only be exercised against doubles written in the same
change -- unfalsifiable by construction. These properties run against the engine SM-R2 ratified, so
a wrong ``WHERE`` clause fails here rather than agreeing with a double that shares its mistake.

Several properties are asserted against **both** the reference double and the real backend, because
the double is the specification the provider tests are written against: if the two disagree, one of
them is wrong and every provider test built on the double is resting on it.
"""

import os

import pytest

pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate, cypher  # noqa: E402
from egpt.memory.substrates import GraphSubstrate  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import (  # noqa: E402
    EntityWrite,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    Scope,
)
from tests.unit.memory._fakes import FakeGraph  # noqa: E402

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")


def _settings():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
    }


@pytest.fixture
def graph():
    """A substrate for the sync tests. Teardown is sync, so nothing here may open the async half.

    `close()` refuses while an async driver is open -- correctly, since it cannot release one -- so a
    test that touched the async half and then failed before its own `aclose()` would have that
    refusal raised in teardown, masking the real failure. Async tests take `async_graph` instead.
    """
    substrate = Neo4jGraphSubstrate(**_settings())
    with substrate._driver.session(database=substrate.database) as session:
        session.run("MATCH (n) DETACH DELETE n")
    yield substrate
    substrate.close()


@pytest.fixture
async def async_graph():
    """The same substrate for tests that drive the async half, with teardown that can await.

    Separate rather than making `graph` async: around thirty sync tests consume it, and an async
    fixture cannot be handed to a sync test function.
    """
    substrate = Neo4jGraphSubstrate(**_settings())
    async with substrate._async_driver().session(database=substrate.database) as session:
        result = await session.run("MATCH (n) DETACH DELETE n")
        await result.consume()
    yield substrate
    await substrate.aclose()


@pytest.fixture(params=["neo4j", "double"])
def any_graph(request, graph):
    """The real backend and the reference double, behind one name.

    A property parametrised over both is what keeps the double honest. A permissive double hides
    provider bugs -- SM-C1's ``FakeVector.scan`` returned live objects, so a provider mutating what
    it scanned was invisible.
    """
    return graph if request.param == "neo4j" else FakeGraph()


def episode(content, id_, scope=ACME, occurred_at=100):
    return MemoryEvent(content=content, scope=scope, id=id_, occurred_at=occurred_at)


def fact(id_, content, scope=ACME, **kwargs):
    record = MemoryRecord(
        id=id_,
        tier=MemoryTier.SEMANTIC,
        content=content,
        scope=scope,
        t_valid=kwargs.pop("t_valid", 100),
        **{k: kwargs.pop(k) for k in ("t_invalid", "expired_at", "superseded_by") if k in kwargs},
    )
    return FactWrite(record=record, **kwargs)


def ids(records):
    return sorted(r.id for r in records)


# -- the acceptance criteria ------------------------------------------------


def test_the_backend_satisfies_the_port(graph):
    """AC 1: reachable via the seam. Structural, as the port is a Protocol."""
    assert isinstance(graph, GraphSubstrate)


def test_an_episode_is_written_and_read_back(any_graph):
    """AC 3, first half."""
    any_graph.upsert_episodes(ACME, [episode("ada moved to berlin", "e1")])

    hits = any_graph.traverse(ACME, MemoryQuery(text="ada moved", tiers=[MemoryTier.EPISODIC]))

    assert ids(hits) == ["e1"]
    assert hits[0].content == "ada moved to berlin"
    assert hits[0].tier is MemoryTier.EPISODIC


def test_an_entity_fact_is_written_and_read_back(graph):
    """AC 3, second half: the triple becomes entity nodes joined by a relation."""
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")])

    hits = graph.traverse(ACME, MemoryQuery(text="ada lives", tiers=[MemoryTier.SEMANTIC]))
    assert ids(hits) == ["f1"]

    with graph._driver.session(database=graph.database) as session:
        row = session.run(
            "MATCH (s:Entity)-[r:REL {id:'f1'}]->(o:Entity) RETURN s.name AS s, r.predicate AS p, o.name AS o"
        ).single()
    assert dict(row) == {"s": "ada", "p": "lives_in", "o": "berlin"}


def test_an_untripled_fact_is_still_storable(graph):
    """SPO is optional structure over the statement text, per the port."""
    graph.upsert_facts(ACME, [fact("f2", "ada is thoughtful about naming")])

    assert ids(graph.traverse(ACME, MemoryQuery(text="ada thoughtful", tiers=[MemoryTier.SEMANTIC]))) == ["f2"]


@pytest.mark.parametrize(
    "writer,reader",
    [(ACME, GLOBEX), (ACME, ACME_BOB), (ACME_BOB, ACME)],
    ids=["across-tenants", "within-tenant-forward", "within-tenant-reverse"],
)
def test_a_scope_that_did_not_write_a_row_cannot_read_it(graph, writer, reader):
    """AC 2, in **both** directions between the same two users.

    Reversing the pair is not symmetry for its own sake: the two scopes are not interchangeable to
    the predicate builder, which emits a clause per slot the *reader* sets. A ``user_id`` dropped
    from one branch leaks only in the direction that branch serves, so testing one direction proves
    the other only by assuming the thing under test.

    SM-R2 section 5.4 ruled the isolation model is a property filter, because Community denies
    ``CREATE DATABASE``. That makes this assertion the isolation guarantee itself, not a detail of it.
    """
    graph.upsert_episodes(writer, [episode("ada salary is 200k", "e1", scope=writer)])
    graph.upsert_facts(
        writer,
        [fact("f1", "ada salary is 200k", scope=writer, subject="ada", predicate="earns", object="200k")],
    )

    query = MemoryQuery(text="ada salary 200k")
    assert graph.traverse(reader, query) == []
    assert graph.bounds_for_ids(reader, ["e1", "f1"]) == {}


def test_a_wider_scope_reads_what_a_narrower_one_wrote(graph):
    """An unset slot means "any", so a tenant-wide read sees both users' rows."""
    graph.upsert_episodes(ACME, [episode("shared topic here", "e1")])
    graph.upsert_episodes(ACME_BOB, [episode("shared topic here", "e2", scope=ACME_BOB)])

    hits = graph.traverse(Scope(tenant_id="acme"), MemoryQuery(text="shared topic"))

    assert ids(hits) == ["e1", "e2"]


def test_the_read_path_makes_no_model_call(graph, monkeypatch):
    """AC 4. Asserted by breaking every outbound HTTP client rather than by reading the code."""
    import httpx

    def explode(*args, **kwargs):
        raise AssertionError("the read path made a network call outside the Bolt driver")

    monkeypatch.setattr(httpx.Client, "send", explode)
    monkeypatch.setattr(httpx.AsyncClient, "send", explode)

    graph.upsert_episodes(ACME, [episode("ada moved to berlin", "e1")])
    assert ids(graph.traverse(ACME, MemoryQuery(text="ada moved"))) == ["e1"]


# -- the invariants ---------------------------------------------------------


def test_a_repeated_write_replaces_the_row_rather_than_appending(any_graph):
    """``PartialWriteError`` tells a caller to retry with the same ids because this is idempotent."""
    any_graph.upsert_episodes(ACME, [episode("first version", "dup")])
    any_graph.upsert_episodes(ACME, [episode("second version", "dup")])

    hits = any_graph.traverse(ACME, MemoryQuery(text="first second version"))

    assert [(r.id, r.content) for r in hits] == [("dup", "second version")]


@pytest.mark.parametrize("shape", ["episode", "fact"])
def test_two_rows_sharing_an_id_in_one_batch_leave_one_live_row(graph, shape):
    """Review finding, reproduced deterministically with a single caller and no concurrency.

    ``OPTIONAL MATCH`` then a conditional ``CREATE`` is a check-then-act race: both rows of one
    ``UNWIND`` observed "no live row for this id" and both created one, so a batch carrying a
    duplicate id produced **two** live rows -- which is not "insert or replace by id" under any
    reading, and made ``traverse`` return the same id twice.

    Both shapes, because the node and relation legs are mechanically distinct statements and the
    fix has to land on each.
    """
    if shape == "episode":
        graph.upsert_episodes(ACME, [episode("version A", "dup"), episode("version B", "dup")])
    else:
        graph.upsert_facts(
            ACME,
            [
                fact("dup", "version A", subject="ada", predicate="says", object="a"),
                fact("dup", "version B", subject="ada", predicate="says", object="a"),
            ],
        )

    hits = graph.traverse(ACME, MemoryQuery(text="version"))

    assert [r.id for r in hits] == ["dup"], "one logical id must have one live row"
    assert hits[0].content == "version B", "last write wins, as replace-by-id means"


def test_the_live_marker_and_expired_at_never_disagree(graph):
    """The marker is a second encoding of "current belief", so the two can drift.

    It exists because a uniqueness constraint cannot say "unique among rows whose ``expired_at`` is
    null" -- but a marker left on an expired row would hold the live slot and hide the successor,
    and one missing from a live row would exempt it from the constraint that makes ``MERGE`` safe.
    Checked after a write, a retirement and an invalidation, which are the three writers.
    """
    graph.upsert_episodes(ACME, [episode("an episode", "e1")])
    graph.upsert_facts(ACME, [fact("f1", "a fact"), fact("f2", "a retired fact", expired_at=500)])
    graph.invalidate(ACME, "f1", t_invalid=300, at=310)

    with graph._driver.session(database=graph.database) as session:
        disagreeing = session.run(
            "MATCH (n) WHERE n.row_id IS NOT NULL AND (n.live IS NULL) <> (n.expired_at IS NOT NULL) "
            "RETURN count(n) AS n "
            "UNION ALL "
            "MATCH ()-[r:REL]->() WHERE (r.live IS NULL) <> (r.expired_at IS NOT NULL) RETURN count(r) AS n"
        )
        counts = [record["n"] for record in disagreeing]

    assert counts == [0, 0], "a row's live marker must agree with its expired_at"


def test_scores_are_similarities_in_the_unit_interval(any_graph):
    """The provider sums the legs' scores, so an undeclared scale decides the ranking by magnitude."""
    any_graph.upsert_episodes(
        ACME, [episode("ada moved to berlin", "e1"), episode("ada moved to berlin last year", "e2")]
    )

    hits = any_graph.traverse(ACME, MemoryQuery(text="ada moved to berlin"))

    assert len(hits) == 2
    assert all(0.0 <= (r.score or 0.0) <= 1.0 for r in hits)
    assert hits[0].score > hits[1].score, "an exact match must outrank a longer one"


def test_the_limit_is_honoured_and_zero_means_zero(any_graph):
    any_graph.upsert_episodes(ACME, [episode(f"shared token {i}", f"e{i}") for i in range(5)])

    assert len(any_graph.traverse(ACME, MemoryQuery(text="shared token", limit=2))) == 2
    assert any_graph.traverse(ACME, MemoryQuery(text="shared token", limit=0)) == []


def test_the_limit_selects_the_best_rows_rather_than_an_arbitrary_page(any_graph):
    """Ranking has to happen **before** the cut, which is why the score is computed server-side.

    Truncating an unordered page returns rows that are merely present rather than relevant, and the
    result still looks like a well-formed answer of the right length.
    """
    # The better match is given the id that sorts *last*, so an ordering that fell back to id would
    # return the other row. With ids in score order this passes whatever the ORDER BY says.
    any_graph.upsert_episodes(
        ACME,
        [
            episode("berlin", "z-best"),
            episode("berlin and a good many other unrelated words besides", "a-worst"),
        ],
    )

    hits = any_graph.traverse(ACME, MemoryQuery(text="berlin", limit=1))

    assert ids(hits) == ["z-best"]


def test_the_limit_applies_to_the_merged_set_not_once_per_shape(any_graph):
    """Episodes, fact nodes and relations are three statements, each honouring ``limit`` itself.

    A caller asking for one record would otherwise get up to three -- one per shape -- which is a
    ``limit`` that means something different depending on how the rows happen to be stored.
    """
    any_graph.upsert_episodes(ACME, [episode("berlin", "e1")])
    any_graph.upsert_facts(ACME, [fact("f1", "berlin")])

    assert len(any_graph.traverse(ACME, MemoryQuery(text="berlin", limit=1))) == 1
    assert len(any_graph.traverse(ACME, MemoryQuery(text="berlin", limit=5))) == 2


def test_a_tier_the_query_did_not_ask_for_is_not_returned(any_graph):
    any_graph.upsert_episodes(ACME, [episode("ada moved to berlin", "e1")])
    any_graph.upsert_facts(ACME, [fact("f1", "ada moved to berlin")])

    query = MemoryQuery(text="ada moved", tiers=[MemoryTier.SEMANTIC])
    assert ids(any_graph.traverse(ACME, query)) == ["f1"]
    assert ids(any_graph.traverse(ACME, MemoryQuery(text="ada moved"), tier=MemoryTier.EPISODIC)) == ["e1"]


def test_invalidation_expires_and_reinserts_rather_than_editing_in_place(graph):
    """SM-R10 section 6 proved in-place wrong: a later fact clobbers an already-set bound.

    Two invalidations must accumulate rows, leave exactly one current belief, apply the tightest
    bound, and leave the original reconstructable.
    """
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")])

    graph.invalidate(ACME, "f1", t_invalid=300, at=310)
    graph.invalidate(ACME, "f1", t_invalid=200, at=320)

    with graph._driver.session(database=graph.database) as session:
        rows = [
            dict(r)
            for r in session.run(
                "MATCH (:Entity)-[r:REL {id:'f1'}]->(:Entity) "
                "RETURN r.t_valid AS t_valid, r.t_invalid AS t_invalid, r.expired_at AS expired_at "
                "ORDER BY coalesce(r.expired_at, 9999999)"
            )
        ]

    assert len(rows) == 3, f"expire-and-reinsert must accumulate rows, got {rows}"
    live = [r for r in rows if r["expired_at"] is None]
    assert len(live) == 1, "exactly one current belief"
    assert live[0]["t_invalid"] == 200, "the tightest bound wins"
    assert rows[0]["t_invalid"] is None, "the original row is untouched and still reconstructable"
    # Folded across revisions too, not only stored correctly: the authority lookup must report the
    # tightest bound it can see, or a fact the graph believes false since 200 is served until 300.
    assert graph.bounds_for_ids(ACME, ["f1"])["f1"].t_invalid == 200


def test_concurrent_invalidation_never_leaves_two_current_beliefs(graph):
    """Review finding: the read that computes the bound and the write that applies it can race.

    Both callers may legitimately read the same live row. What must not happen is both inserting a
    successor, which would leave one logical fact with two rows claiming to be current belief --
    the state SCD-2 exists to prevent. Either one wins and the other is a no-op, or the loser fails
    loudly on the uniqueness constraint; the one outcome ruled out is silent corruption.
    """
    import threading

    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", t_valid=100)])

    errors: list = []
    barrier = threading.Barrier(2)

    def invalidate(bound: int) -> None:
        worker = Neo4jGraphSubstrate(**_settings(), auto_init_schema=False)
        try:
            barrier.wait(timeout=10)
            worker.invalidate(ACME, "f1", t_invalid=bound, at=310)
        except Exception as exc:  # a loud failure is an accepted outcome; corruption is not
            errors.append(exc)
        finally:
            worker.close()

    threads = [threading.Thread(target=invalidate, args=(bound,)) for bound in (300, 400)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    with graph._driver.session(database=graph.database) as session:
        live = [
            dict(record)
            for record in session.run(
                "MATCH (n:Fact {id:'f1'}) WHERE n.expired_at IS NULL RETURN n.t_invalid AS t_invalid"
            )
        ]

    assert len(live) == 1, f"one logical fact must have one current belief, got {live} (errors: {errors})"
    assert live[0]["t_invalid"] in (300, 400), "the surviving row must carry a bound one caller asked for"


def test_a_bounded_copy_is_pinned_to_the_expiry_its_own_call_performed(graph):
    """The loser of an invalidation race must not copy the row the winner expired.

    Both halves of an invalidation run in one transaction, so within a call the row this copies is
    always the row this call just expired -- which is why the guard reads as redundant. It is not:
    a second caller whose expire found nothing (the winner already did it) would otherwise still
    match the winner's row by ``row_id`` and insert a second successor, leaving two rows claiming
    current belief. The uniqueness constraint would catch that, but as a hard error rather than the
    no-op it should be.

    Driven by issuing the loser's statement directly with its own ``at``, because a threaded race
    cannot be made to land on that interleaving on demand.
    """
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", t_valid=100)])
    graph.invalidate(ACME, "f1", t_invalid=300, at=310)

    with graph._driver.session(database=graph.database) as session:
        winner_row_id = session.run(
            "MATCH (n:Fact {id:'f1'}) WHERE n.expired_at = 310 RETURN n.row_id AS row_id"
        ).single()["row_id"]

        # The losing caller: same row, a different transaction time, its own expire a no-op.
        for text, binds in cypher.insert_bounded_copy_statements(ACME):
            session.run(text, **binds, live_row_id=winner_row_id, at=320, t_invalid=250, row_id="loser-row")

        live = [
            dict(r) for r in session.run("MATCH (n:Fact {id:'f1'}) WHERE n.expired_at IS NULL RETURN n.row_id AS r")
        ]

    assert len(live) == 1, f"the loser must not add a second current belief, got {live}"
    assert live[0]["r"] != "loser-row"


def test_the_bounded_copy_keeps_the_logical_id(graph):
    """A surrogate key on the successor would break the merge's dedup-by-id.

    That dedup is the only thing carrying the graph's bound onto the vector leg's stale projection,
    so a fresh id means the invalidated fact reads as current on an ordinary read.
    """
    graph.upsert_facts(ACME, [fact("f1", "ada works at acme")])

    graph.invalidate(ACME, "f1", t_invalid=300, at=310)

    with graph._driver.session(database=graph.database) as session:
        stored = {r["id"] for r in session.run("MATCH (n:Fact) RETURN n.id AS id")}
        count = session.run("MATCH (n:Fact) RETURN count(n) AS n").single()["n"]
    assert stored == {"f1"} and count == 2


def test_bounds_report_a_bound_that_ranking_hid(graph):
    """The precision failure ``bounds_for_ids`` exists for: a false fact served as current belief.

    The successor row carries the same content, so it scores identically and falls off the page.
    """
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")])
    graph.invalidate(ACME, "f1", t_invalid=300, at=310)

    bounds = graph.bounds_for_ids(ACME, ["f1", "never-written"])

    assert set(bounds) == {"f1"}, "an unknown id is absent, not a null entry"
    assert bounds["f1"].t_invalid == 300
    assert bounds["f1"].expired_at is None


def test_an_invalidation_that_would_leave_the_row_valid_at_no_instant_is_refused(graph):
    """Valid time is half-open, so such a bound is written, receipted, and then invisible forever."""
    graph.upsert_facts(ACME, [fact("f1", "ada works at acme", t_valid=100)])

    with pytest.raises(ValueError, match="valid at no instant"):
        graph.invalidate(ACME, "f1", t_invalid=100, at=310)


def test_invalidating_an_episode_is_refused_rather_than_obliged(graph):
    """ADR 0003 section B.6 gives an episodic row a different lifecycle from a graph edge.

    The port says so in as many words -- "do not route episode archival here" -- and an earlier
    revision of this backend obliged anyway, with this file's own tests doing exactly what the
    contract forbids. A refusal that names the owning lifecycle is what makes the boundary real.
    """
    graph.upsert_episodes(ACME, [episode("ada works at acme", "e1")])

    with pytest.raises(MemoryOperationError, match="governs semantic contradiction on graph edges"):
        graph.invalidate(ACME, "e1", t_invalid=300, at=310)

    assert graph.bounds_for_ids(ACME, ["e1"])["e1"].t_invalid is None, "the refusal wrote nothing"


def test_an_invalidation_under_a_foreign_scope_does_nothing(graph):
    """Scoped like every other statement, so a mis-scoped invalidation is observable rather than silent."""
    graph.upsert_facts(ACME, [fact("f1", "ada works at acme")])

    graph.invalidate(GLOBEX, "f1", t_invalid=300, at=310)

    assert graph.bounds_for_ids(ACME, ["f1"])["f1"].t_invalid is None


def test_a_fact_is_retired_by_writing_its_transaction_time_fields_back(graph):
    """A backend that dropped them makes retirement a silent no-op with two current beliefs."""
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin")])

    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", expired_at=500, superseded_by="f2")])

    bounds = graph.bounds_for_ids(ACME, ["f1"])["f1"]
    assert (bounds.expired_at, bounds.superseded_by) == (500, "f2")
    assert graph.traverse(ACME, MemoryQuery(text="ada lives berlin")) == [], "a retired fact is not current belief"


def test_provenance_survives_the_round_trip(graph):
    """SM-R7 section 7 makes ``source_episode_ids`` load-bearing: it is the fact-to-episode audit trail."""
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", source_episode_ids=["e1", "e2"])])

    hit = graph.traverse(ACME, MemoryQuery(text="ada lives berlin"))[0]

    assert hit.provenance["source_episode_ids"] == ["e1", "e2"]


def test_an_empty_batch_is_a_no_op_rather_than_an_error(any_graph):
    any_graph.upsert_episodes(ACME, [])
    any_graph.upsert_facts(ACME, [])

    assert any_graph.bounds_for_ids(ACME, []) == {}


def test_a_write_under_a_foreign_scope_is_refused(graph):
    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        graph.upsert_episodes(ACME, [episode("x", "e1", scope=GLOBEX)])


# -- sync and async agree ---------------------------------------------------


async def test_both_halves_leave_the_same_durable_state(async_graph):
    """Not "an async variant exists": the same decisions and the same rows.

    SM-C1 shipped ``write``/``awrite`` persisting different rows for one identical failure, twice,
    in different rounds, so the comparison is on committed state rather than on return values.
    """

    def snapshot():
        with async_graph._driver.session(database=async_graph.database) as session:
            # Sorted by `repr`, because the rows carry nulls and tuple ordering compares an int
            # against None the moment two rows differ on a bound.
            return sorted(
                repr(sorted(dict(r).items(), key=lambda kv: kv[0]))
                for r in session.run(
                    "MATCH (n) WHERE n.id IS NOT NULL "
                    "RETURN labels(n)[0] AS label, n.id AS id, n.content AS content, "
                    "n.t_invalid AS t_invalid, n.expired_at AS expired_at "
                    "UNION ALL "
                    "MATCH ()-[r:REL]->() RETURN 'REL' AS label, r.id AS id, r.content AS content, "
                    "r.t_invalid AS t_invalid, r.expired_at AS expired_at"
                )
            )

    async_graph.upsert_episodes(ACME, [episode("ada moved to berlin", "e1")])
    async_graph.upsert_facts(
        ACME, [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")]
    )
    async_graph.invalidate(ACME, "f1", t_invalid=300, at=310)
    sync_state = snapshot()

    with async_graph._driver.session(database=async_graph.database) as session:
        session.run("MATCH (n) DETACH DELETE n")

    await async_graph.aupsert_episodes(ACME, [episode("ada moved to berlin", "e1")])
    await async_graph.aupsert_facts(
        ACME, [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")]
    )
    await async_graph.ainvalidate(ACME, "f1", t_invalid=300, at=310)

    assert snapshot() == sync_state


async def test_the_async_reads_agree_with_the_sync_ones(async_graph):
    async_graph.upsert_episodes(ACME, [episode("ada moved to berlin", "e1")])
    query = MemoryQuery(text="ada moved")

    assert ids(await async_graph.atraverse(ACME, query)) == ids(async_graph.traverse(ACME, query))
    assert await async_graph.abounds_for_ids(ACME, ["e1"]) == async_graph.bounds_for_ids(ACME, ["e1"])


# -- lifecycle --------------------------------------------------------------


async def test_aclose_releases_the_async_pool_rather_than_dropping_the_reference():
    """Review finding, reproduced as ``ResourceWarning: unclosed AsyncBoltDriver``.

    Setting ``_adriver = None`` looks like closing and is not: the pool's sockets and background
    tasks stay open, and the dropped reference makes the leak unreachable rather than fixed.
    """
    import gc
    import warnings

    substrate = Neo4jGraphSubstrate(**_settings())
    await substrate.aupsert_episodes(ACME, [episode("an episode", "e1")])

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await substrate.aclose()
        gc.collect()

    leaks = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
    assert not leaks, f"aclose() left resources open: {leaks}"
    assert substrate._adriver is None


async def test_sync_close_refuses_while_an_async_driver_is_open():
    """A sync call cannot release it, and pretending to is what produced the leak above.

    An async driver binds to the loop that created it, so ``close()`` can neither await it nor
    drive that loop -- the refusal names ``aclose()`` rather than silently doing the wrong thing.
    """
    substrate = Neo4jGraphSubstrate(**_settings())
    await substrate.aupsert_episodes(ACME, [episode("an episode", "e1")])

    with pytest.raises(MemoryOperationError, match="aclose"):
        substrate.close()

    await substrate.aclose()


def test_the_provider_closes_its_substrates_through_the_seam(graph):
    """The seam owns the lifecycle; a caller should not reach for `provider.graph.close()`."""
    from egpt.memory.adapters.co_located import CoLocatedMemoryProvider

    provider = CoLocatedMemoryProvider(graph=graph)
    provider.close()

    with pytest.raises(Exception):
        graph.traverse(ACME, MemoryQuery(text="anything"))


async def test_ainit_schema_actually_creates_the_schema(async_graph):
    """The async half's statements must land, not merely be issued.

    ``session.run`` is lazy: an unconsumed result leaves the statement's completion up to whatever
    the session does on close. It does land today -- which is why no leak warning reproduces -- but
    the assertion here is on the constraints existing, not on the calls being made.
    """
    with async_graph._driver.session(database=async_graph.database) as session:
        for record in list(session.run("SHOW CONSTRAINTS YIELD name RETURN name")):
            session.run(f"DROP CONSTRAINT {record['name']} IF EXISTS").consume()
        assert session.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS n").single()["n"] == 0

    await async_graph.ainit_schema()

    with async_graph._driver.session(database=async_graph.database) as session:
        created = session.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS n").single()["n"]
    assert created > 0, "ainit_schema issued its statements without them taking effect"


def test_init_schema_creates_the_entity_key_and_vector_indexes(graph):
    """Review finding on PR #89, round 2: without these, SM-G8's (#106) exact-match grouping on
    ``key`` and similarity match on ``embedding`` are full scans of every ``:Entity`` node across
    every scope. A string check on the schema statement is not proof it takes effect -- this reads
    the index back from the engine, including its type.
    """
    # Only the two under test, not every index: `egpt_entity_identity` is constraint-backed and
    # `DROP INDEX` on it fails with `IndexDropFailed` -- it must be dropped as a constraint, which
    # is a different statement and not what this test is checking.
    with graph._driver.session(database=graph.database) as session:
        session.run("DROP INDEX egpt_entity_key IF EXISTS").consume()
        session.run("DROP INDEX egpt_entity_embedding IF EXISTS").consume()

    graph.init_schema()

    with graph._driver.session(database=graph.database) as session:
        indexes = {r["name"]: r["type"] for r in session.run("SHOW INDEXES YIELD name, type RETURN name, type")}
    assert indexes.get("egpt_entity_key") == "RANGE"
    assert indexes.get("egpt_entity_embedding") == "VECTOR"


def test_a_row_whose_json_was_written_out_of_band_is_refused_not_crashed(graph):
    """The read path's half of the error taxonomy the write path already had.

    Reachable only by something other than this backend writing the row, which is exactly when a
    raw ``JSONDecodeError`` three frames inside a read is least useful.
    """
    graph.upsert_episodes(ACME, [episode("ada moved to berlin", "e1")])
    with graph._driver.session(database=graph.database) as session:
        session.run("MATCH (n:Episode {id:'e1'}) SET n.metadata = 'not json at all'").consume()

    with pytest.raises(MemoryOperationError, match="not readable JSON"):
        graph.traverse(ACME, MemoryQuery(text="ada moved"))


async def test_the_credential_does_not_outlive_the_drivers_that_need_it(async_graph):
    """A substrate reaches logs and crash dumps through ``vars()`` more often than anyone intends."""
    await async_graph.aupsert_episodes(ACME, [episode("an episode", "e1")])

    rendered = repr(vars(async_graph))

    assert _settings()["password"] not in rendered, "the password is still an attribute after both drivers exist"


def test_a_sync_only_substrate_also_forgets_its_credential():
    """The half the first version missed -- and the one most substrates actually are.

    Clearing it when the *async* driver is built covers nothing for a caller that never touches the
    async API, which is the default path. Both close routes now forget it, so no route leaves the
    password on the object.
    """
    substrate = Neo4jGraphSubstrate(**_settings())
    substrate.upsert_episodes(ACME, [episode("an episode", "e1")])
    assert substrate._adriver is None, "this test is only meaningful on a substrate with no async driver"

    substrate.close()

    assert _settings()["password"] not in repr(vars(substrate))


def test_init_schema_actually_creates_the_schema(graph):
    """The sync twin of the async schema test, asserting the effect rather than the calls.

    ``session.run`` is lazy on this half too; the async test existed alone, which is how the sync
    path ended up relying on the drain-on-close its own twin's docstring warns against.
    """
    with graph._driver.session(database=graph.database) as session:
        for record in list(session.run("SHOW CONSTRAINTS YIELD name RETURN name")):
            session.run(f"DROP CONSTRAINT {record['name']} IF EXISTS").consume()
        assert session.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS n").single()["n"] == 0

    graph.init_schema()

    with graph._driver.session(database=graph.database) as session:
        created = session.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS n").single()["n"]
    assert created > 0, "init_schema issued its statements without them taking effect"


# -- anchor-seeded multi-hop traversal (SM-G9 #107) --------------------------


def test_multihop_traversal_reaches_a_fact_via_a_neighbouring_entity(graph):
    """Supersedes ``test_traversal_is_single_hop_until_sm_g9``, which pinned exactly this gap and
    named its own replacement: "SM-G9 has landed multi-hop traversal -- update this ticket's
    boundary." Asking about ada now surfaces what is known about berlin, joined by one edge.
    """
    graph.upsert_facts(
        ACME,
        [
            fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin"),
            fact("f2", "berlin sits within germany", subject="berlin", predicate="located_in", object="germany"),
        ],
    )

    hits = {r.id: r for r in graph.traverse(ACME, MemoryQuery(text="ada"))}

    # Membership, not order: `ids()` sorts, so it could not have asserted ranking either way.
    # `f1` (hop 1, and lexically overlapping "ada" too) must outrank `f2` (hop 2, no overlap) --
    # asserted on score directly rather than an id order that would silently stop meaning anything
    # the day either fact's content changes (round-1 review).
    assert set(hits) == {"f1", "f2"}
    assert hits["f1"].score > hits["f2"].score, "a nearer hop must outrank a farther one"


def test_a_bare_relation_endpoint_with_no_explicit_entity_upsert_is_still_a_valid_anchor(graph):
    """``RelationExtractionPipeline`` (SM-G3 #101) calls only ``upsert_facts``, never
    ``upsert_entities`` -- the endpoint nodes ``upsert_facts_statements``'s triple leg merges carry
    a ``name`` but no normalised ``key``, since only ``upsert_entities_statement`` writes that.
    Anchor selection keyed on ``key`` alone would make every such node permanently unreachable,
    silently missing most production-extracted relations -- this is the regression that guards it.
    """
    graph.upsert_facts(
        ACME,
        [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")],
    )
    with graph._driver.session(database=graph.database) as session:
        key = session.run("MATCH (n:Entity {name: 'ada'}) RETURN n.key AS key").single()["key"]
    assert key is None, "setup check: this node must be the bare endpoint case, not an explicit upsert"

    hits = graph.traverse(ACME, MemoryQuery(text="tell me about ada"))

    assert "f1" in ids(hits)


def test_traverse_reconstructs_each_rows_own_write_scope_under_a_tenant_wide_read(graph):
    """Round-1 review, Major: ``_record`` used to leave every ``traverse`` row's ``scope`` as the
    *call* scope, not its own write scope -- correct for the isolation post-condition alone, but
    wrong for ``Neo4jGraphSubstrate._merge_hits``'s ``(record.scope.key(), record.id)`` dedup key,
    which exists specifically so two different users' rows can share one id under a tenant-wide read
    without colliding. With the call scope constant across the whole call, that key degenerated to a
    bare id, and the second writer's row silently disappeared.

    Two users, same id, same call -- the exact collision the dedup key exists to prevent.
    """
    ada, bob = ACME, ACME_BOB
    graph.upsert_facts(
        ada,
        [fact("shared-id", "ada earns 200k", scope=ada, subject="ada", predicate="earns", object="200k")],
    )
    graph.upsert_facts(
        bob,
        [fact("shared-id", "bob earns 150k", scope=bob, subject="bob", predicate="earns", object="150k")],
    )

    hits = {r.scope: r for r in graph.traverse(Scope(tenant_id="acme"), MemoryQuery(text="earns"))}

    assert set(hits) == {ada, bob}, "both writers' rows must survive the (scope, id) dedup"
    assert hits[ada].content == "ada earns 200k"
    assert hits[bob].content == "bob earns 150k"


def test_multihop_traversal_scopes_every_intermediate_node_not_only_the_far_end(graph):
    """Round-1 review, Major: only ``other.scope_key = anchor.scope_key`` (the walk's far end) was
    checked; an intermediate node on a 2+-hop path had no scope predicate of its own.

    A 1-hop bridge into a foreign scope is already caught by the pre-existing ``other`` check --
    for that path, the foreign node *is* ``other``. Isolating the actual gap needs a path where the
    *intermediate* node is foreign-scoped but the walk happens to land back in the anchor's own
    scope: ``ada -[edge1]-> mole (globex) -[edge2]-> secret (acme)``. Without the
    ``nodes(path)[1..-1]`` guard, that 2-hop path passes every other check (``other`` = ``secret``,
    same scope as ``anchor``) and ``edge2`` would be walkable.

    **Unexploitable through the public write API** -- every write scopes both of an edge's
    endpoints under the same call, so no genuinely cross-scope edge like ``edge1`` can exist to
    walk through. This proves the query-level guard itself, not a reachable production bug: both
    edges are created directly via raw Cypher (bypassing every write-path invariant that would
    normally prevent this), with every scope property an edge would otherwise carry set to ada's
    own -- isolating the assertion to the node-scope guard specifically, not the pre-existing
    edge-scope or ``other``-scope checks, which would otherwise also explain a pass.

    **A positive witness edge, not only the negative assertion (round-2 review, Critical).** A
    purely negative ``assert "edge2-mole-secret" not in hits`` passes vacuously if the whole leg
    silently returns nothing -- for any reason, not only the guard under test. ``friend``/
    ``workplace`` sit entirely inside ada's own scope, two genuine hops from the anchor with no
    lexical overlap with the query at all, so ``workplace-edge`` appearing in ``hits`` is direct
    evidence the multi-hop leg walked two hops and returned something on *this* call, not merely
    that a foreign edge happened to be absent.
    """
    ada, globex = ACME, GLOBEX
    graph.upsert_entities(
        ada,
        [
            EntityWrite(name="ada", entity_type="person", scope=ada),
            EntityWrite(name="secret", entity_type="thing", scope=ada),
            EntityWrite(name="friend", entity_type="person", scope=ada),
            EntityWrite(name="workplace", entity_type="thing", scope=ada),
        ],
    )
    graph.upsert_entities(globex, [EntityWrite(name="mole", entity_type="person", scope=globex)])
    graph.upsert_facts(
        ada,
        [
            fact(
                "friend-edge",
                "it is a close relationship",
                scope=ada,
                subject="ada",
                predicate="knows",
                object="friend",
            ),
            fact(
                "workplace-edge",
                "it operates five days a week",
                scope=ada,
                subject="friend",
                predicate="works_at",
                object="workplace",
            ),
        ],
    )

    def rel_props(edge_id, subject, object_):
        return {
            "id": edge_id,
            "row_id": f"{edge_id}-row",
            "live": True,
            "tier": "semantic",
            "content": f"{subject} knows {object_}",
            "scope_key": ada.key(),
            "tenant_id": ada.tenant_id,
            "user_id": ada.user_id,
            "predicate": "knows",
            "t_valid": 100,
            "created_at": 100,
            "provenance": "{}",
            "metadata": "{}",
        }

    with graph._driver.session(database=graph.database) as session:
        session.run(
            "MATCH (a:Entity {scope_key: $ada_key, name: 'ada'}), (m:Entity {scope_key: $globex_key, name: 'mole'}) "
            "CREATE (a)-[r:REL]->(m) SET r = $props",
            ada_key=ada.key(),
            globex_key=globex.key(),
            props=rel_props("edge1-ada-mole", "ada", "mole"),
        )
        session.run(
            "MATCH (m:Entity {scope_key: $globex_key, name: 'mole'}), (s:Entity {scope_key: $ada_key, name: 'secret'}) "
            "CREATE (m)-[r:REL]->(s) SET r = $props",
            ada_key=ada.key(),
            globex_key=globex.key(),
            props=rel_props("edge2-mole-secret", "mole", "secret"),
        )

    hits = ids(graph.traverse(ada, MemoryQuery(text="tell me about ada")))

    assert "workplace-edge" in hits, "the multi-hop leg must still be reaching genuine two-hop, in-scope edges"
    assert "edge2-mole-secret" not in hits, "the walk must not cross the foreign-scoped intermediate node"


def test_multihop_traversal_scopes_every_intermediate_edge_too(graph):
    """Round-2 review, Minor: the ``all(rel IN relationships(path) ...)`` guard checked
    current-belief and the window on every path edge, but not each edge's own ``scope_key`` --
    that was left to the post-``UNWIND`` ``rel_predicate``, which only decides whether *that*
    edge is *returned*, not whether the path may be walked *through* it to reach a farther one.

    Distinct from ``test_multihop_traversal_scopes_every_intermediate_node_not_only_the_far_end``:
    here every *node* on the path is correctly ada-scoped (so the node guard has nothing to catch)
    and only the *intermediate edge's own* ``scope_key`` property is wrong -- a data-integrity
    anomaly, not a reachable write-path bug (a genuine write always stamps an edge's `scope_key`
    from the same call as both its endpoints), constructed here with raw Cypher to isolate this
    guard specifically. Without it, the pre-existing post-``UNWIND`` ``rel_predicate`` still keeps
    the mis-scoped edge itself from being *returned* (it checks the individual ``SCOPE_SLOTS``
    properties, which this edge's own content lexically matching "ada" would otherwise also make
    reachable through the unrelated flat leg -- not this finding's concern, so the edge's content
    here avoids the anchor word entirely). What only the new guard prevents is the walk continuing
    *through* the mis-scoped edge to reach ``edge2`` -- the same "walk through a violation to reach
    something legitimate" shape as the node case.
    """
    ada = ACME
    graph.upsert_entities(
        ada,
        [
            EntityWrite(name="ada", entity_type="person", scope=ada),
            EntityWrite(name="mid", entity_type="thing", scope=ada),
            EntityWrite(name="far", entity_type="thing", scope=ada),
            EntityWrite(name="colleague", entity_type="person", scope=ada),
        ],
    )
    # A witness edge, fully in-scope and reachable at hop 1 -- positive evidence the leg still
    # runs on this call, not only that the anomalous edge happens to be absent (round-2 review).
    graph.upsert_facts(
        ada,
        [
            fact(
                "witness-edge", "it has worked together for years", subject="ada", predicate="knows", object="colleague"
            )
        ],
    )

    def rel_props(edge_id, content, scope_key):
        # `content` deliberately avoids "ada" -- the flat direct-content leg scopes by the
        # individual SCOPE_SLOTS properties, not `scope_key`, so a mis-scoped-by-`scope_key`-alone
        # edge whose content lexically matches the query is still visible through that unrelated
        # leg. Not this finding's concern; excluded here so it cannot explain either assertion.
        return {
            "id": edge_id,
            "row_id": f"{edge_id}-row",
            "live": True,
            "tier": "semantic",
            "content": content,
            "scope_key": scope_key,
            "tenant_id": ada.tenant_id,
            "user_id": ada.user_id,
            "predicate": "knows",
            "t_valid": 100,
            "created_at": 100,
            "provenance": "{}",
            "metadata": "{}",
        }

    with graph._driver.session(database=graph.database) as session:
        # edge1's endpoints are both correctly ada-scoped nodes -- only the edge's own `scope_key`
        # property is wrong, so the node-level guards have nothing to reject here.
        session.run(
            "MATCH (a:Entity {scope_key: $ada_key, name: 'ada'}), (m:Entity {scope_key: $ada_key, name: 'mid'}) "
            "CREATE (a)-[r:REL]->(m) SET r = $props",
            ada_key=ada.key(),
            props=rel_props("edge1-wrong-scope", "a first connection", "not-a-real-scope-key"),
        )
        session.run(
            "MATCH (m:Entity {scope_key: $ada_key, name: 'mid'}), (f:Entity {scope_key: $ada_key, name: 'far'}) "
            "CREATE (m)-[r:REL]->(f) SET r = $props",
            ada_key=ada.key(),
            props=rel_props("edge2-past-the-anomaly", "a second connection", ada.key()),
        )

    hits = ids(graph.traverse(ada, MemoryQuery(text="tell me about ada")))

    assert "witness-edge" in hits, "the multi-hop leg must still be reaching genuine in-scope edges"
    assert "edge2-past-the-anomaly" not in hits, "the walk must not cross the mis-scoped edge to reach a farther one"


def test_multihop_traversal_respects_the_configured_hop_bound(graph):
    """The bound is set at construction (``max_traverse_hops``), not per call -- SM-G9 closes it
    at the source rather than trusting a caller to pass a smaller value."""
    settings = _settings()
    bounded = Neo4jGraphSubstrate(**settings, max_traverse_hops=1)
    try:
        bounded.upsert_facts(
            ACME,
            [
                fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin"),
                fact("f2", "berlin sits within germany", subject="berlin", predicate="located_in", object="germany"),
            ],
        )
        hits = bounded.traverse(ACME, MemoryQuery(text="ada"))
        assert ids(hits) == ["f1"], "hop 2 is beyond the configured bound"
    finally:
        bounded.close()


def test_multihop_traversal_caps_the_anchor_set(graph):
    """Round-1 review, Major: ``max_hops`` alone does not bound this leg's cost, since a common
    query word matches every entity whose name contains it. Three entities all match the query
    "acme"; capped to 2, only the two that sort first by name become anchors, so only their edges
    are walkable.

    Each fact's content is phrased without "acme" in it at all -- content repeating the subject
    name would also satisfy the flat direct-content leg regardless of anchor selection, which would
    not isolate this leg's own cap (the same pitfall the unit-level version of this test named).
    """
    settings = _settings()
    bounded = Neo4jGraphSubstrate(**settings, max_traverse_anchor_entities=2)
    try:
        bounded.upsert_entities(
            ACME,
            [
                EntityWrite(name="acme alpha", entity_type="company", scope=ACME),
                EntityWrite(name="acme beta", entity_type="company", scope=ACME),
                EntityWrite(name="acme gamma", entity_type="company", scope=ACME),
            ],
        )
        bounded.upsert_facts(
            ACME,
            [
                fact(
                    "f-alpha",
                    "it runs the widget business",
                    subject="acme alpha",
                    predicate="operates",
                    object="widgets",
                ),
                fact(
                    "f-beta", "it runs the gadget business", subject="acme beta", predicate="operates", object="gadgets"
                ),
                fact(
                    "f-gamma", "it runs the gizmo business", subject="acme gamma", predicate="operates", object="gizmos"
                ),
            ],
        )

        hits = ids(bounded.traverse(ACME, MemoryQuery(text="tell me about acme")))

        # Positive assertions first (round-2 review, Critical): a purely negative check passes
        # vacuously if the whole leg returns nothing (broken Cypher, an unbound parameter), which
        # proves nothing about the cap operating at 2 rather than at 0.
        assert "f-alpha" in hits and "f-beta" in hits, "the two lower-sorted anchors must still reach their edges"
        # Deterministic by name: "acme alpha" and "acme beta" sort before "acme gamma".
        assert "f-gamma" not in hits, "the third-sorted anchor must be cut by the cap"
    finally:
        bounded.close()


def test_multihop_traversal_respects_the_valid_time_window(graph):
    """The window shapes the walk itself, not only what is returned -- an out-of-window edge cannot
    be crossed to reach a farther hop, matching how ``VectorSubstrate.search`` narrows in-search."""
    graph.upsert_facts(
        ACME,
        [
            # `t_invalid=200` closes the interval to [100, 200) -- genuinely outside [400, 600).
            # An open-ended `t_valid` alone would still overlap: `ValidWindow.overlaps` reads an
            # unset upper bound as valid through the present, which does intersect a later window.
            fact(
                "f1",
                "ada lives in berlin",
                subject="ada",
                predicate="lives_in",
                object="berlin",
                t_valid=100,
                t_invalid=200,
            ),
            fact(
                "f2",
                "berlin sits within germany",
                subject="berlin",
                predicate="located_in",
                object="germany",
                t_valid=500,
            ),
        ],
    )

    windowed = MemoryQuery(text="ada", valid_from=400, valid_to=600)
    assert graph.traverse(ACME, windowed) == [], "f1 (hop 1) is outside the window, so f2 is unreachable through it"

    unwindowed = MemoryQuery(text="ada")
    assert ids(graph.traverse(ACME, unwindowed)) == ["f1", "f2"]


async def test_sync_and_async_traverse_agree(async_graph):
    """Named to match ``test_sync_and_async_traversal_agree`` in the unit suite (round-1 review:
    the previous name, ``test_amultihop_traversal_agrees_with_the_sync_half``, read as exercising
    only the async half, when it calls both)."""
    await async_graph.aupsert_facts(
        ACME,
        [
            fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin"),
            fact("f2", "berlin sits within germany", subject="berlin", predicate="located_in", object="germany"),
        ],
    )

    query = MemoryQuery(text="ada")
    assert ids(await async_graph.atraverse(ACME, query)) == ids(async_graph.traverse(ACME, query))


def test_the_graph_is_not_enumerable(graph):
    """``scan`` is deliberately absent from ``GraphSubstrate``: what enumeration means over a
    temporal graph (current belief? every revision? edges or nodes?) is SM-G9's (#107) to define."""
    assert not hasattr(graph, "scan")


# -- entity nodes (SM-G2 #100) ----------------------------------------------


def entity_write(name="acme corp", scope=ACME, **kwargs):
    kwargs.setdefault("entity_type", "company")
    return EntityWrite(name=name, scope=scope, **kwargs)


def read_entity(graph, name, scope=ACME):
    """Read an entity node back, from the real store or the double, as one dict.

    There is no read verb for entity nodes on the port: what a graph read returns is
    ``traverse``'s, and querying the semantic subgraph is SM-G7 (#105) / SM-G9 (#107). Raw Cypher
    is what keeps this test from depending on a verb this ticket did not add, and the double's
    branch is what lets a property be asserted against **both**, which is this file's whole bargain.
    """
    if isinstance(graph, FakeGraph):
        stored = graph.entities.get((scope.key(), name))
        return (
            None
            if stored is None
            else {
                "entity_type": stored.entity_type,
                "description": stored.description,
                "key": stored.key,
                "embedding": stored.embedding,
                "source_episode_ids": stored.source_episode_ids,
                "aliases": stored.aliases,
            }
        )
    with graph._driver.session(database=graph.database) as session:
        row = session.run(
            "MATCH (n:Entity {scope_key: $scope_key, name: $name}) RETURN properties(n) AS props",
            scope_key=scope.key(),
            name=name,
        ).single()
    return dict(row["props"]) if row else None


def test_an_entity_is_written_and_read_back_as_a_node(any_graph):
    """SM-G2's first criterion, against the engine as well as the double."""
    any_graph.upsert_entities(
        ACME,
        [entity_write(description="a company", aliases=["Acme"], source_episode_ids=["e1"], embedding=[0.1, 0.2])],
    )

    props = read_entity(any_graph, "acme corp")
    assert props["entity_type"] == "company"
    assert props["description"] == "a company"
    assert props["key"] == "acme corp"
    assert props["embedding"] == [0.1, 0.2]
    assert props["source_episode_ids"] == ["e1"]


def test_a_second_sighting_unions_provenance_rather_than_replacing_it(graph):
    """SM-R7 section 7's audit trail, which every other write on this backend would overwrite."""
    graph.upsert_entities(ACME, [entity_write(source_episode_ids=["e1"], aliases=["Acme"])])
    graph.upsert_entities(ACME, [entity_write(source_episode_ids=["e2"], aliases=["ACME"])])

    props = read_entity(graph, "acme corp")
    assert sorted(props["source_episode_ids"]) == ["e1", "e2"]
    assert sorted(props["aliases"]) == ["ACME", "Acme"]


def test_re_extracting_the_same_episode_adds_nothing(graph):
    """The union has to be idempotent, or a scheduler re-running a pass grows the list forever.

    Nothing marks an episode consumed until SM-L1 (#122), so a re-run is the expected case rather
    than an unlucky one.
    """
    for _ in range(3):
        graph.upsert_entities(ACME, [entity_write(source_episode_ids=["e1"], aliases=["Acme"])])

    props = read_entity(graph, "acme corp")
    assert props["source_episode_ids"] == ["e1"]
    assert props["aliases"] == ["Acme"]


def test_the_first_sighting_keeps_created_at_while_updated_at_moves(graph):
    graph.upsert_entities(ACME, [entity_write(source_episode_ids=["e1"])])
    first = read_entity(graph, "acme corp")

    graph.upsert_entities(ACME, [entity_write(source_episode_ids=["e2"], description="later")])
    second = read_entity(graph, "acme corp")

    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]
    assert second["description"] == "later", "everything outside the two unioned lists is last-write"


def test_a_description_only_re_upsert_never_erases_an_existing_embedding(graph):
    """Review finding on PR #96 (SM-G8): a caller re-upserting an entity to update one field --
    here, ``description``, with ``embedding`` left at its default ``None`` -- must not erase the
    vector a previous write already stored. ``SET n += row.props`` deletes a property whose row
    value is ``null``, so ``_entity_rows`` has to omit the key rather than pass it through.
    """
    graph.upsert_entities(ACME, [entity_write(embedding=[0.1, 0.2])])

    graph.upsert_entities(ACME, [entity_write(description="later")])

    props = read_entity(graph, "acme corp")
    assert props["embedding"] == [0.1, 0.2]
    assert props["description"] == "later"


def test_created_at_is_set_when_a_triple_endpoint_created_the_node_first(graph):
    """Review finding on PR #89: ``upsert_facts`` MERGEs an entity's endpoint node with no
    ``created_at`` at all. The literal ``ON CREATE SET n.created_at = row.created_at`` this method
    used to run never fires on a node that already exists -- so an entity SM-G3 writes before this
    pipeline extracts it kept ``created_at = null`` forever, permanently breaking SM-G8's (#106)
    first-seen ordering for every entity that reaches the graph via a relation first.
    """
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")])
    assert read_entity(graph, "ada") is not None  # sanity: the endpoint node exists, created_at unset

    graph.upsert_entities(ACME, [entity_write(name="ada", entity_type="person", source_episode_ids=["e1"])])

    props = read_entity(graph, "ada")
    assert props["created_at"] is not None


def test_an_entity_write_and_a_triple_endpoint_are_the_same_node(graph):
    """The identity claim in the port's docstring, checked rather than asserted in prose.

    ``upsert_facts`` merges its endpoints on ``(scope_key, name)``. If ``upsert_entities`` used a
    different key -- the normalised one, say -- every triple would create a second, propertyless
    node for the same entity and SM-G8 (#106) would inherit duplicates this ticket manufactured.
    """
    graph.upsert_facts(ACME, [fact("f1", "ada lives in berlin", subject="ada", predicate="lives_in", object="berlin")])
    graph.upsert_entities(ACME, [entity_write(name="ada", entity_type="person", source_episode_ids=["e1"])])

    with graph._driver.session(database=graph.database) as session:
        count = session.run(
            "MATCH (n:Entity {scope_key: $scope_key, name: 'ada'}) RETURN count(n) AS n", scope_key=ACME.key()
        ).single()["n"]
    assert count == 1
    assert read_entity(graph, "ada")["entity_type"] == "person"


def test_two_scopes_holding_one_name_are_two_nodes(graph):
    """Resolving one user's "berlin" against another's is not dedup, it is a cross-scope leak."""
    graph.upsert_entities(ACME, [entity_write(name="berlin", entity_type="city", description="ada's")])
    graph.upsert_entities(
        ACME_BOB, [entity_write(name="berlin", entity_type="city", description="bob's", scope=ACME_BOB)]
    )

    assert read_entity(graph, "berlin", ACME)["description"] == "ada's"
    assert read_entity(graph, "berlin", ACME_BOB)["description"] == "bob's"


def test_an_entity_node_carries_no_bi_temporal_field_in_the_store(graph):
    """ADR 0003 section 1: ``SemanticEntity`` is embeddable, not bi-temporal."""
    graph.upsert_entities(ACME, [entity_write(source_episode_ids=["e1"])])

    props = read_entity(graph, "acme corp")
    assert not {"t_valid", "t_invalid", "expired_at", "superseded_by", cypher.LIVE, "tier"} & set(props)


async def test_an_empty_entity_batch_is_a_no_op_rather_than_a_round_trip(async_graph, monkeypatch):
    """The port's empty-is-a-no-op rule, held for the new verb as for the other two.

    Asserted on whether the driver was **asked**, not on the row count: ``UNWIND [] AS row`` writes
    nothing either way, so a count-only assertion passes against a backend that opens a session and
    runs a statement per empty batch -- which over a drained tier is one round trip per pass.
    """
    calls = []
    monkeypatch.setattr(async_graph, "_run", lambda *a, **k: calls.append("sync"))
    monkeypatch.setattr(async_graph, "_arun", lambda *a, **k: calls.append("async"))

    async_graph.upsert_entities(ACME, [])
    await async_graph.aupsert_entities(ACME, [])

    assert calls == []


def test_an_entity_write_under_a_foreign_scope_is_refused(graph):
    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        graph.upsert_entities(ACME, [entity_write(scope=GLOBEX)])


async def test_both_entity_halves_leave_the_same_durable_state(async_graph):
    """Parity on committed rows, not on the two verbs existing."""
    async_graph.upsert_entities(ACME, [entity_write(source_episode_ids=["e1"], aliases=["Acme"])])
    sync_props = read_entity(async_graph, "acme corp")

    with async_graph._driver.session(database=async_graph.database) as session:
        session.run("MATCH (n) DETACH DELETE n")

    await async_graph.aupsert_entities(ACME, [entity_write(source_episode_ids=["e1"], aliases=["Acme"])])
    async_props = read_entity(async_graph, "acme corp")

    assert async_props and sync_props
    assert {k: v for k, v in async_props.items() if k not in ("created_at", "updated_at")} == {
        k: v for k, v in sync_props.items() if k not in ("created_at", "updated_at")
    }
