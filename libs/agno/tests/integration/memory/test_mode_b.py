"""Mode B -- external memory services, against real backends (SM-X2 #138).

Gated on **both** ``EGPT_INTEGRATION_PGVECTOR`` and ``EGPT_INTEGRATION_NEO4J``; start the engines
with ``cookbook/scripts/run_pgvector.sh`` and ``cookbook/scripts/run_neo4j.sh``. The direct sibling
of ``test_mode_a.py`` (SM-X1 #137) -- same round trip, same tenant-isolation property, same two
engines -- built against ``provider="external"`` / ``mode=FRONTIER`` instead of
``provider="co_located"`` / ``mode=LOCAL``.

**What this proves that the unit-level swap suite cannot.** ``test_provider_swap.py`` says so
itself: every config there, ``external`` included, is built over ``FakeVector``/``FakeGraph`` --
seam *shape*, not a network. ADR 0003 section 5 asks SM-C1 to re-prove config-only swap against a
**real** external backend, and its own record (SPEC section 11.2) is explicit that a Mode B adapter
built before SM-V1/SM-G1 landed "would have composed the same two ports over the same in-memory
doubles. That proves a class can be subclassed, not that a network seam holds." This file is that
proof for Mode B: a real write -> consolidate -> read pass over pgvector and Neo4j, reached through
``provider="external"``, and the two extra properties ADR 0003 section 5 names by name --

* whether a real backend's failure/timeout forces a change to ``MemoryProvider`` or the two
  substrate ports (it does not: :func:`test_a_leg_erroring_fails_the_read_fast_without_a_partial_
  result` and :func:`test_a_leg_timing_out_fails_the_read_fast_within_the_configured_bound` exercise
  ``aorchestrate``'s documented bare-``gather`` contract -- "a leg failure aborts the read,
  propagating immediately rather than settling both first" -- against a real refused connection
  rather than a mocked one);
* whether ``aread``'s ``asyncio.gather`` fan-out is real concurrency or two round trips in a coat
  (:func:`test_the_two_read_legs_run_concurrently_not_sequentially` measures wall-clock against
  each real backend's own latency rather than trusting that ``gather`` was actually used).

**Deliberately not built here (owned elsewhere), matching test_mode_a.py's own list:**

* Per-tenant *selection* of Mode A vs Mode B is SM-C5's (#93); this file always builds one static
  ``provider="external"`` config, the same way ``test_mode_a.py`` always builds one static
  ``provider="co_located"`` config.
* A distinct external SaaS vector/graph service. This sandbox has no such thing to point at, and
  the seam does not need one to prove its claim: a real ``VectorSubstrate``/``GraphSubstrate``
  reached over a real TCP socket, with real serialization and a real driver, is what
  ``resolve_substrate`` promises Mode B either way -- "co-located" vs "external" is a deployment
  topology decision (which host the DSN/URI names), not a code path this seam can vary. What
  ``provider="external"`` adds beyond that is exercised directly: the registry key resolves to
  ``ExternalMemoryProvider``, ``mode=LOCAL`` is refused (unit-covered in ``test_provider_swap.py``,
  not repeated here), and every diagnostic is stamped ``deployment: "external"``.
* Real extraction (entity/relation/fact pipelines) -- ``test_mode_a.py``'s own scope-out applies
  unchanged; consolidation here runs the same deterministic ``_distil`` default.
"""

import asyncio
import os
import socket
import time
from hashlib import blake2b
from typing import Dict

import pytest

pytest.importorskip("psycopg", reason="the pgvector memory substrate connects through psycopg 3")
pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

pytestmark = pytest.mark.skipif(
    not (os.getenv("EGPT_INTEGRATION_PGVECTOR") and os.getenv("EGPT_INTEGRATION_NEO4J")),
    reason="needs both EGPT_INTEGRATION_PGVECTOR and EGPT_INTEGRATION_NEO4J",
)

from neo4j.exceptions import ServiceUnavailable  # noqa: E402
from sqlalchemy import text  # noqa: E402

from egpt.integrations.memory.vector.pgvector import sql  # noqa: E402
from egpt.memory import MemoryProviderConfig, build_memory_provider  # noqa: E402
from egpt.memory.adapters.external import ExternalMemoryProvider  # noqa: E402
from egpt.memory.config import MemoryConfigResolver, MemoryProviderConfigError, build_memory_provider_for  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope, Substrate  # noqa: E402
from egpt.providers.model_provider import ProviderMode  # noqa: E402
from tests.unit.memory.test_provider_swap import aconsumer_workload, consumer_workload  # noqa: E402

PG_TABLE = "test_mode_b_vectors"
ACME = Scope(tenant_id="acme", user_id="ada")
GLOBEX = Scope(tenant_id="globex", user_id="bob")


class OfflineTokenEmbedder:
    """See ``test_mode_a.py``'s identical class for why: a deterministic, in-process embedder that
    declares itself Mode-eligible rather than one this suite would have to download. Mode B does
    not require an *online* embedder -- ``ProviderMode.FRONTIER`` only lifts the air-gap refusal,
    it never mandates crossing the network for the embedding step itself -- so reusing the same
    offline embedder here keeps this file's only variable the mode/provider pairing, not a second,
    unrelated one (a live network call whose flakiness would leak into a seam test)."""

    dimensions = 32
    offline_capable = True

    def get_embedding(self, text_):
        vector = [0.0] * self.dimensions
        for token in text_.lower().split():
            vector[int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big") % self.dimensions] += 1.0
        return vector

    async def async_get_embedding(self, text_):
        await asyncio.sleep(0)
        return self.get_embedding(text_)


def _pg_url():
    return os.getenv("EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai")


def _neo4j_settings():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
    }


def _mode_b_config() -> MemoryProviderConfig:
    """One config, both legs real, ``provider="external"`` + ``mode=FRONTIER`` (Mode B). The only
    construction call site is ``build_memory_provider`` -- nothing here names
    ``ExternalMemoryProvider`` directly, mirroring ``test_mode_a.py``'s own ``_mode_a_config``."""
    return MemoryProviderConfig(
        provider="external",
        mode=ProviderMode.FRONTIER,
        vector={
            "backend": "pgvector",
            "db_url": _pg_url(),
            "embedder": OfflineTokenEmbedder(),
            "dim": OfflineTokenEmbedder.dimensions,
            "table": PG_TABLE,
            "auto_init_schema": True,
        },
        graph={"backend": "neo4j", **_neo4j_settings()},
    )


@pytest.fixture
def provider():
    """A Mode B provider over real backends, cleared at setup so one test's leftover rows cannot
    leak into the next -- the same drop-before-yield split ``test_mode_a.py``'s ``provider`` uses.

    **Blast radius** (round-4 review, PR #180): the unscoped ``MATCH (n) DETACH DELETE n`` below
    (and its twin on ``async_provider``) shares one Neo4j container's state with ``test_mode_a.py``
    and ``test_e2e_symbiotic_loop.py``, both of which run the same unscoped wipe -- pre-existing,
    not introduced here, but this file is now the third sharing that risk. Under a concurrent
    runner (e.g. ``pytest-xdist``) one file's setup could wipe rows another file's test just wrote.
    Not fixed here: scoping every teardown to its own tenant prefix is a cross-file change these
    three files would need to make together."""
    built = build_memory_provider(_mode_b_config())
    assert isinstance(built, ExternalMemoryProvider)  # the factory this key resolves to, sanity-checked once
    with built.vector._engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {sql.qualified('ai', PG_TABLE)}"))
    with built.graph._driver.session(database=built.graph.database) as session:
        session.run("MATCH (n) DETACH DELETE n")
    yield built
    built.close()


@pytest.fixture
async def async_provider():
    """Independently built, for the async half -- see ``test_mode_a.py``'s ``async_provider`` for
    why sharing one instance across sync and async teardown is avoided. See ``provider``'s own
    docstring for the unscoped-teardown blast radius this fixture shares with it."""
    built = build_memory_provider(_mode_b_config())
    with built.vector._engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {sql.qualified('ai', PG_TABLE)}"))
    with built.graph._driver.session(database=built.graph.database) as session:
        session.run("MATCH (n) DETACH DELETE n")
    yield built
    await built.aclose()


# ---------------------------------------------------------------------------
# Config-only swap, re-proved against real backends (ADR 0003 section 5)
# ---------------------------------------------------------------------------


def test_build_memory_provider_wires_both_real_substrates_in_frontier_mode(provider):
    assert provider.substrates() == {Substrate.VECTOR: True, Substrate.GRAPH: True}
    assert provider.config.mode is ProviderMode.FRONTIER
    assert provider.provider_key == "external"


def test_config_only_swap_reaches_a_real_external_provider(provider):
    """The exact structural proof ``test_provider_swap.py`` leaves open for a real Mode B backend:
    the same ``consumer_workload`` function, unmodified, driving a provider built from real
    pgvector + real Neo4j through ``provider="external"`` rather than ``FakeVector``/``FakeGraph``.
    If the seam leaked reality through, this function -- which knows nothing but the
    ``MemoryProvider`` ABC -- would be the one to notice."""
    result = consumer_workload(provider, ACME)

    assert result["accepted"] == 2
    assert "email over phone" in result["context"]
    assert result["substrates"] == {"vector": True, "graph": True}
    assert result["consolidated"] == ACME.tenant_id


async def test_the_async_config_only_swap_reaches_a_real_external_provider(async_provider):
    """Async half of the same structural proof, mirroring ``test_mode_a.py``'s own async twin."""
    result = await aconsumer_workload(async_provider, GLOBEX)

    assert result["accepted"] == 1
    assert "the customer" in result["context"]


def test_mode_a_and_mode_b_agree_on_the_same_workload_over_the_same_engines(provider):
    """The literal config-only-swap claim: **the same two engines**, driven through the two
    different registry keys / modes, produce the same consumer-visible result for an equivalent
    write. This is what "Mode A and Mode B are the same seam over different services" means when
    there is only one pair of real services in the room -- the topology Mode A vs Mode B actually
    varies (which host a DSN/URI names) is not this sandbox's to prove; the seam behaving
    identically regardless of which registry key reached these two engines is."""
    from egpt.memory.adapters.co_located import CoLocatedMemoryProvider

    mode_a_config = MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.LOCAL,
        vector={
            "backend": "pgvector",
            "db_url": _pg_url(),
            "embedder": OfflineTokenEmbedder(),
            "dim": OfflineTokenEmbedder.dimensions,
            "table": PG_TABLE,
            "auto_init_schema": True,
        },
        graph={"backend": "neo4j", **_neo4j_settings()},
    )
    mode_a = build_memory_provider(mode_a_config)
    assert isinstance(mode_a, CoLocatedMemoryProvider)
    try:
        acme_a = Scope(tenant_id="acme-a", user_id="ada")
        acme_b = Scope(tenant_id="acme-b", user_id="ada")
        result_a = consumer_workload(mode_a, acme_a)
        result_b = consumer_workload(provider, acme_b)

        assert result_a["accepted"] == result_b["accepted"]
        assert result_a["tiers_written"] == result_b["tiers_written"]
        assert result_a["substrates"] == result_b["substrates"]
    finally:
        mode_a.close()


def test_external_under_mode_local_is_refused_against_the_real_registry():
    """The construction-time refusal ``test_provider_swap.py`` covers with doubles, re-checked here
    so it is also proved against the real ``discovery``/``resolve_substrate`` path this file uses
    for everything else -- a mismatch this severe should not depend on which fixture caught it."""
    with pytest.raises(MemoryProviderConfigError, match="FRONTIER"):
        build_memory_provider(
            MemoryProviderConfig(
                provider="external",
                mode=ProviderMode.LOCAL,
                vector={
                    "backend": "pgvector",
                    "db_url": _pg_url(),
                    "embedder": OfflineTokenEmbedder(),
                    "dim": OfflineTokenEmbedder.dimensions,
                    "table": PG_TABLE,
                    "auto_init_schema": True,
                },
                graph={"backend": "neo4j", **_neo4j_settings()},
            )
        )


def test_build_memory_provider_for_resolves_a_real_external_provider_by_tenant():
    """SM-C5's (#93) own acceptance criterion is "a tenant switches Mode A<->B by config" -- and
    every other test in this file, plus ``test_provider_swap.py``, reaches
    ``ExternalMemoryProvider`` only through ``build_memory_provider(config)`` directly.
    ``MemoryConfigResolver``/``build_memory_provider_for`` is the actual per-tenant resolution path
    that criterion describes, and nothing built one against ``"external"``/``"co_located"`` over a
    real backend before this (round-4 review, elevated minor, PR #180) --
    ``test_provider_per_tenant.py`` exercises the resolver extensively, but only against an
    unrelated ``"recording"`` test double."""
    resolver = MemoryConfigResolver(policies={"acme": _mode_b_config()})
    built = build_memory_provider_for(resolver, tenant_id="acme")
    try:
        assert isinstance(built, ExternalMemoryProvider)
        assert built.config.mode is ProviderMode.FRONTIER
    finally:
        built.close()


# ---------------------------------------------------------------------------
# The episodic -> semantic round trip, over both real engines
# ---------------------------------------------------------------------------


def test_write_consolidate_read_round_trips_through_both_engines(provider):
    """The Mode B twin of ``test_mode_a.py``'s identical test: write raw episodes, consolidate
    into facts, read the result back, with both a real HNSW search and a real Cypher query
    agreeing on what came out -- this time reached through ``provider="external"``."""
    provider.write(
        ACME,
        [
            MemoryEvent(content="the deploy pipeline moved to a managed provider", scope=ACME, id="e1"),
            MemoryEvent(content="the deploy pipeline moved to a managed provider", scope=ACME, id="e2"),
        ],
    )

    report = provider.consolidate(ACME)
    assert report.episodic_scanned == 2
    assert report.duplicates_merged == 1
    assert report.semantic_written == 1

    slice_ = provider.read(ACME, MemoryQuery(text="deploy pipeline managed provider", tiers=[MemoryTier.SEMANTIC]))
    assert slice_.records, "consolidation wrote a fact but the fused read found nothing"
    assert all(r.tier is MemoryTier.SEMANTIC for r in slice_.records)
    # The one Mode B diagnostic this seam adds (`ExternalMemoryProvider._fusion_extra`).
    assert slice_.fusion["deployment"] == "external"

    vector_hits = provider.vector.search(ACME, MemoryQuery(text="deploy pipeline", tiers=[MemoryTier.SEMANTIC]))
    assert vector_hits, "the fact never reached pgvector"
    with provider.graph._driver.session(database=provider.graph.database) as session:
        count = session.run(
            "MATCH (f:Fact {tenant_id: $tenant_id}) RETURN count(f) AS n", tenant_id=ACME.tenant_id
        ).single()["n"]
    assert count == 1, f"expected exactly one distilled fact on the graph leg, got {count}"


async def test_async_write_consolidate_read_round_trips_through_both_engines(async_provider):
    """Async twin of the round trip above, same reasoning as ``test_mode_a.py``'s: a provider
    whose async half quietly delegated to a blocking one would pass the sync test and hang an
    event loop in production, invisibly, until something under load noticed."""
    await async_provider.awrite(
        GLOBEX,
        [MemoryEvent(content="the on-call rotation moved to a managed provider", scope=GLOBEX, id="g1")],
    )

    report = await async_provider.aconsolidate(GLOBEX)
    assert report.episodic_scanned == 1
    assert report.semantic_written == 1

    slice_ = await async_provider.aread(
        GLOBEX, MemoryQuery(text="on-call rotation managed provider", tiers=[MemoryTier.SEMANTIC])
    )
    assert slice_.records
    assert all(r.tier is MemoryTier.SEMANTIC for r in slice_.records)
    assert slice_.fusion["deployment"] == "external"


def test_tenant_isolation_holds_on_real_engines(provider):
    """The property a double can share a bug on (SM-C4 #92), checked against a real Postgres
    ``WHERE`` and a real Cypher tenant predicate, reached through Mode B this time -- same property
    ``test_mode_a.py`` proves for Mode A, so isolation is known to hold on both sides of the
    config-only swap, not just the one SM-X1 already covered."""
    provider.write(ACME, [MemoryEvent(content="acme deploy secret", scope=ACME, id="a1")])
    provider.write(GLOBEX, [MemoryEvent(content="globex deploy secret", scope=GLOBEX, id="b1")])
    provider.consolidate(ACME)
    provider.consolidate(GLOBEX)

    acme_seen = provider.read(ACME, MemoryQuery(text="deploy secret")).records
    globex_seen = provider.read(GLOBEX, MemoryQuery(text="deploy secret")).records

    assert any(r.content.startswith("acme") for r in acme_seen), "acme's own fact went missing from its own read"
    assert not any(r.content.startswith("globex") for r in acme_seen), "globex's fact leaked into acme's read"

    assert any(r.content.startswith("globex") for r in globex_seen), "globex's own fact went missing from its own read"
    assert not any(r.content.startswith("acme") for r in globex_seen), "acme's fact leaked into globex's read"


# ---------------------------------------------------------------------------
# What a real network changes: latency, concurrency, and partial-leg failure
# (ADR 0003 section 5's own callout -- see the module docstring)
# ---------------------------------------------------------------------------


def _reserve_unused_port() -> int:
    """A TCP port nothing is listening on, for a real connection-refused failure. Not a mock: the
    socket layer really does refuse the connection, the same failure an actually-down external
    service produces.

    **Narrow TOCTOU window** (round-4 review, PR #180): the probe socket is closed here, before the
    Neo4j driver ever tries to connect, so something else in a shared network namespace (CI's
    included) could in principle claim the port in the gap between the two. Low practical risk on a
    high, ephemeral loopback port, and there is no portable way to reserve-without-closing a port a
    *different* process (the driver's own connect) then reuses -- flagged rather than fixed."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def _listening_but_unresponsive_port():
    """A port that completes the TCP handshake and then never speaks Bolt -- a real **hang**,
    unlike :func:`_reserve_unused_port`'s socket, which is closed before Neo4j ever reaches it and
    so only ever produces an immediate refusal.

    ``listen()`` alone is enough: the kernel's own accept backlog completes the three-way handshake
    on this process's behalf even though nothing here ever calls ``accept()``, so a client's
    ``connect()`` returns successfully and the connection then sits open with no bytes flowing in
    either direction -- exactly what a genuinely wedged (not merely down) external service looks
    like on the wire."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


@pytest.fixture
async def one_leg_unreachable_provider():
    """A Mode B provider whose vector leg is real pgvector and whose graph leg names a Neo4j Bolt
    port nothing is listening on -- an unreachable "external service" without mocking anything in
    the seam or the driver. A loopback port with no listener refuses the connection (``ECONNREFUSED``)
    near-instantly rather than hanging, so no custom driver timeout is needed to keep this fast; the
    Neo4j substrate's constructor does not accept one (``uri``/``username``/``password``/``database``
    plus a handful of traversal-shape knobs, no transport timeout). Async-only: the sync path's
    ``orchestrate`` calls the two legs in sequence, so it has no concurrent-fan-out behaviour to
    observe (module docstring, ``retrieval/orchestrator.py``'s own)."""
    dead_port = _reserve_unused_port()
    config = MemoryProviderConfig(
        provider="external",
        mode=ProviderMode.FRONTIER,
        vector={
            "backend": "pgvector",
            "db_url": _pg_url(),
            "embedder": OfflineTokenEmbedder(),
            "dim": OfflineTokenEmbedder.dimensions,
            "table": PG_TABLE,
            "auto_init_schema": True,
        },
        graph={
            "backend": "neo4j",
            "uri": f"bolt://127.0.0.1:{dead_port}",
            "username": "neo4j",
            "password": "wrong",
            # Construction otherwise runs schema init eagerly (unlike the vector leg's deferred
            # `create_schema`) and would fail *here*, at build time, rather than on the read this
            # fixture exists to exercise -- a `MemoryProviderConfigError` from `build_memory_provider`,
            # not the read-path failure `aorchestrate` documents. Deferring it is what makes this an
            # unreachable-at-read-time leg instead of an unreachable-at-construction-time one.
            "auto_init_schema": False,
        },
    )
    built = build_memory_provider(config)
    with built.vector._engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {sql.qualified('ai', PG_TABLE)}"))
    yield built
    await built.aclose()


@pytest.fixture
async def one_leg_hanging_provider(_listening_but_unresponsive_port):
    """Same shape as :func:`one_leg_unreachable_provider`, except the graph leg's port genuinely
    **hangs** (see :func:`_listening_but_unresponsive_port`) rather than refusing outright -- what
    :func:`test_a_leg_timing_out_fails_the_read_fast_within_the_configured_bound`'s own docstring
    has always claimed to exercise, and what ``one_leg_unreachable_provider``'s ``ECONNREFUSED``
    port cannot produce (round-4 review, PR #180)."""
    config = MemoryProviderConfig(
        provider="external",
        mode=ProviderMode.FRONTIER,
        vector={
            "backend": "pgvector",
            "db_url": _pg_url(),
            "embedder": OfflineTokenEmbedder(),
            "dim": OfflineTokenEmbedder.dimensions,
            "table": PG_TABLE,
            "auto_init_schema": True,
        },
        graph={
            "backend": "neo4j",
            "uri": f"bolt://127.0.0.1:{_listening_but_unresponsive_port}",
            "username": "neo4j",
            "password": "wrong",
            # See `one_leg_unreachable_provider`'s identical comment: deferred so the failure this
            # fixture exists to exercise happens on the read, not at construction.
            "auto_init_schema": False,
        },
    )
    built = build_memory_provider(config)
    with built.vector._engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {sql.qualified('ai', PG_TABLE)}"))
    yield built
    await built.aclose()


async def test_a_leg_erroring_fails_the_read_fast_without_a_partial_result(one_leg_unreachable_provider):
    """``aorchestrate``'s own docstring states the contract: "a leg failure aborts the read,
    propagating immediately rather than settling both first... a partial ``RetrievalLegs`` ... is a
    wrong answer, not a smaller one." This is that contract against a real socket that really
    refuses the connection, not ``asyncio.gather(..., return_exceptions=True)`` mocked to look like
    one. If a real backend's failure had forced ``MemoryProvider``/the substrate ports to grow
    partial-result semantics, this is where it would have shown up."""
    provider = one_leg_unreachable_provider

    # `(MemoryOperationError, OSError, Exception)` used to stand here -- vacuous, since `Exception`
    # is already a superclass of the other two, so the tuple was equivalent to `pytest.raises
    # (Exception)` (round-4 review finding). `_translated`'s docstring (`neo4j.py`) is explicit that
    # a `ServiceUnavailable` connection refusal propagates **unchanged**, never becoming this
    # module's `MemoryOperationError` -- naming it here is what actually pins that contract instead
    # of accepting whatever this fixture happens to raise.
    with pytest.raises(ServiceUnavailable):
        await provider.aread(ACME, MemoryQuery(text="a fact worth reading back", tiers=[MemoryTier.SEMANTIC]))


@pytest.mark.timeout(20)  # safety net: a driver that ignores `wait_for`'s cancellation must not wedge the suite
async def test_a_leg_timing_out_fails_the_read_fast_within_the_configured_bound(one_leg_hanging_provider):
    """The timeout half of the same claim: a leg that hangs rather than immediately refusing still
    surfaces within the (short, test-configured) bound the *caller* wraps it in, rather than the
    read hanging forever or silently returning the other leg's candidates alone.

    Previously this reused ``one_leg_unreachable_provider`` -- the identical fixture the adjacent
    "erroring" test above uses, built from a closed loopback socket that refuses the connection
    (``ECONNREFUSED``) near-instantly. That can never hang, so the ``wait_for`` bound below did no
    work: the docstring claimed to test a timeout while the fixture could only ever produce a
    refusal, an internal contradiction between what this test says and what it proves (round-4
    review, Major, PR #180). ``one_leg_hanging_provider`` genuinely wedges the connection instead
    (see its own docstring), so the bound below is load-bearing: deleting the ``wait_for`` wrapper
    now makes this test hang rather than pass.

    There is no driver-level connect timeout to bound this from the *Neo4j substrate's* side (its
    constructor takes no such knob -- see ``one_leg_unreachable_provider``'s comment); the only
    thing standing between a genuinely wedged leg and an indefinite hang is this ``asyncio.wait_for``
    at the caller."""
    provider = one_leg_hanging_provider
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            provider.aread(ACME, MemoryQuery(text="anything", tiers=[MemoryTier.SEMANTIC])),
            timeout=4.0,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"the read took {elapsed:.1f}s to fail -- the wait_for bound did not hold it"


async def test_the_two_read_legs_run_concurrently_not_sequentially(async_provider, monkeypatch):
    """``aorchestrate``'s module docstring claims the vector and graph calls "run concurrently
    under ``asyncio.gather``". A wall-clock comparison (fused vs. sum-of-legs) is not a reliable
    way to prove that against two real backends on loopback -- both legs answer in a few
    milliseconds, well inside the noise floor of process scheduling, so "fused is faster than
    sequential" does not reliably separate from measurement jitter at this latency.

    Proved directly instead: wrap each leg's real async call to record when it started and
    finished (the backend round trip itself is untouched -- this only times it), drive one
    ``aread``, and assert the two legs' time windows **overlap**. Two calls that ran one after the
    other cannot produce overlapping windows regardless of how fast either one is; two calls
    genuinely fanned out under ``asyncio.gather`` are in flight at the same instant, which this
    catches whether the backends take microseconds or seconds.
    """
    windows: Dict[str, tuple] = {}
    orig_vector_call = async_provider.vector.asearch_candidates
    orig_graph_call = async_provider.graph.atraverse

    async def timed_vector_call(*args, **kwargs):
        start = time.monotonic()
        try:
            return await orig_vector_call(*args, **kwargs)
        finally:
            windows["vector"] = (start, time.monotonic())

    async def timed_graph_call(*args, **kwargs):
        start = time.monotonic()
        try:
            return await orig_graph_call(*args, **kwargs)
        finally:
            windows["graph"] = (start, time.monotonic())

    monkeypatch.setattr(async_provider.vector, "asearch_candidates", timed_vector_call)
    monkeypatch.setattr(async_provider.graph, "atraverse", timed_graph_call)

    await async_provider.awrite(ACME, [MemoryEvent(content="a latency probe fact", scope=ACME, id="lat-1")])
    await async_provider.aread(ACME, MemoryQuery(text="latency probe", tiers=[MemoryTier.EPISODIC]))

    assert set(windows) == {"vector", "graph"}, f"expected both legs to run, only saw {sorted(windows)}"
    v_start, v_end = windows["vector"]
    g_start, g_end = windows["graph"]
    overlap_start, overlap_end = max(v_start, g_start), min(v_end, g_end)
    assert overlap_end > overlap_start, (
        f"vector leg ran [{v_start:.6f}, {v_end:.6f}], graph leg ran [{g_start:.6f}, {g_end:.6f}] -- "
        "the windows do not overlap, so the two legs ran sequentially rather than concurrently"
    )


async def test_the_async_verbs_never_fall_back_to_their_blocking_sync_twins(async_provider, monkeypatch):
    """The gap the test above does not close (round-4 review, Major, PR #180): rewriting
    ``awrite``, ``aread_tier`` or ``aconsolidate`` to call their sync twins (``self.vector.upsert``
    instead of ``aupsert``, ``self.graph.traverse`` instead of ``atraverse``, and so on) left every
    suite in this repo green, including this one, byte-identical to baseline -- concurrency is
    proved for ``aread``'s vector/graph fan-out above, but nothing proved the other three async
    verbs were even on the async path, let alone concurrent. In production that fallback is
    blocking I/O on the event loop, the exact failure this codebase elsewhere spends
    ``asyncio.to_thread`` to avoid.

    Proved here directly rather than by timing: each verb's *sync* substrate methods are
    monkeypatched to raise if called at all, so a verb that quietly delegates to blocking I/O fails
    loudly and immediately instead of merely being slower than it should be."""

    def _forbidden(name):
        def _raise(*args, **kwargs):
            raise AssertionError(
                f"{name} was called from an async verb -- it should have called its async twin "
                "instead of falling back to blocking I/O on the event loop."
            )

        return _raise

    monkeypatch.setattr(async_provider.vector, "upsert", _forbidden("vector.upsert"))
    monkeypatch.setattr(async_provider.vector, "search_candidates", _forbidden("vector.search_candidates"))
    monkeypatch.setattr(async_provider.vector, "scan", _forbidden("vector.scan"))
    monkeypatch.setattr(async_provider.graph, "upsert_episodes", _forbidden("graph.upsert_episodes"))
    monkeypatch.setattr(async_provider.graph, "upsert_facts", _forbidden("graph.upsert_facts"))
    monkeypatch.setattr(async_provider.graph, "traverse", _forbidden("graph.traverse"))

    await async_provider.awrite(
        ACME, [MemoryEvent(content="the async-only write probe moved to a managed provider", scope=ACME, id="aw-1")]
    )
    records = await async_provider.aread_tier(ACME, MemoryTier.EPISODIC, MemoryQuery(text="async-only write probe"))
    assert records, "aread_tier found nothing -- the forbidden-sync-method setup broke the read, not just guarded it"

    report = await async_provider.aconsolidate(ACME)
    assert report.semantic_written == 1
