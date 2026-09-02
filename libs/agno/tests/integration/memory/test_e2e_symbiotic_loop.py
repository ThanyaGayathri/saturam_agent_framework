"""End-to-end symbiotic memory loop: interaction -> write-back -> consolidation -> retrieval ->
context, for both deployment modes, against real backends (SM-X10 #146).

Component suites each prove one seam in isolation:

* ``test_memory_provider_run_wiring.py`` (unit) proves the agent-runtime hooks (SM-X3 #139) fire,
  against a ``MagicMock`` provider -- it never touches a real substrate.
* ``test_mode_a.py`` (integration) proves ``write`` -> ``consolidate`` -> ``read`` round-trips
  through real pgvector + real Neo4j -- but it calls the provider directly, never through an
  ``Agent`` turn, so it does not prove the runtime hooks and the real backends agree with each
  other.

Neither proves the whole loop: an ``Agent`` turn writing back through the real hooks, landing on
real substrates, consolidating into real semantic facts, and a *later* turn's feed-forward read
actually injecting that fact into the messages sent to the model. That gap is this file's job.

**Both modes, each through its own registered adapter.** Mode B now has its real external-service
adapter (SM-X2 #138, ``egpt.memory.adapters.external.ExternalMemoryProvider``, ``config.provider=
"external"``), so this file selects the adapter by ``config.provider`` the same way a tenant's
Mode A/B switch (SM-C5 #93) would, rather than building ``co_located`` with ``mode=FRONTIER`` as a
stand-in for an adapter that did not exist yet. Both cases still run over the same real ephemeral
pgvector + Neo4j -- what differs between them is which registered provider key resolves, and the
deployment-mode flag each one threads through (``resolve_substrate``'s air-gap check, embedder /
reranker eligibility, and ``ExternalMemoryProvider``'s own ``deployment: "external"`` diagnostics
stamp) -- not the backend under test.

Gated on **both** ``EGPT_INTEGRATION_PGVECTOR`` and ``EGPT_INTEGRATION_NEO4J``; start the engines
with ``cookbook/scripts/run_pgvector.sh`` and ``cookbook/scripts/run_neo4j.sh``.
"""

import os
from hashlib import blake2b
from typing import Any, AsyncIterator, Iterator, List

import pytest

pytest.importorskip("psycopg", reason="the pgvector memory substrate connects through psycopg 3")
pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

pytestmark = pytest.mark.skipif(
    not (os.getenv("EGPT_INTEGRATION_PGVECTOR") and os.getenv("EGPT_INTEGRATION_NEO4J")),
    reason="needs both EGPT_INTEGRATION_PGVECTOR and EGPT_INTEGRATION_NEO4J",
)

from sqlalchemy import text  # noqa: E402

from egpt.agent.agent import Agent  # noqa: E402
from egpt.integrations.memory.vector.pgvector import sql  # noqa: E402
from egpt.memory import MemoryProviderConfig, MemoryQuery, MemoryTier, build_memory_provider  # noqa: E402
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider  # noqa: E402
from egpt.memory.adapters.external import ExternalMemoryProvider  # noqa: E402
from egpt.memory.types import Scope, Substrate  # noqa: E402
from egpt.models.base import Model  # noqa: E402
from egpt.models.message import MessageMetrics  # noqa: E402
from egpt.models.response import ModelResponse  # noqa: E402
from egpt.providers.model_provider import ProviderMode  # noqa: E402
from egpt.run.base import RunContext  # noqa: E402

PG_TABLE = "test_e2e_loop_vectors"

#: One config per mode, both proved against this file's turns. Not a fixture parameter shared
#: with ``test_mode_a.py``: that file's ``PG_TABLE``/tenants are its own, and reusing them would
#: make the two suites interfere if ever run against the same live engines concurrently.
_MODES = pytest.mark.parametrize("mode", [ProviderMode.LOCAL, ProviderMode.FRONTIER], ids=["mode_a", "mode_b"])


class OfflineTokenEmbedder:
    """A deterministic bag-of-tokens embedder, declaring itself Mode A-eligible.

    Matches ``test_mode_a.py``'s embedder of the same name: which real embedder Mode A defaults to
    is SM-V2's (#96), and it is a multi-hundred-MB download this suite should not require to run.
    ``offline_capable`` is the escape hatch that lets one embedder serve both ``LOCAL`` and
    ``FRONTIER`` configs here, since the air-gap check only rejects a *network-reaching* embedder
    for ``LOCAL`` -- it never requires one for ``FRONTIER``.
    """

    dimensions = 32
    offline_capable = True

    def get_embedding(self, text_):
        # `blake2b`, not the builtin `hash()`, for a hash stable across processes -- matches
        # `test_vector_substrate.py`'s and `test_mode_a.py`'s `TokenEmbedder`s.
        vector = [0.0] * self.dimensions
        for token in text_.lower().split():
            vector[int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big") % self.dimensions] += 1.0
        return vector

    async def async_get_embedding(self, text_):
        import asyncio

        await asyncio.sleep(0)
        return self.get_embedding(text_)


class ScriptedModel(Model):
    """An offline model that plays back a fixed script of replies, one per ``invoke``/``ainvoke``.

    Records every ``messages`` list it was called with, so a test can assert on what the runtime
    hooks actually injected before the model call -- not just what the provider returned in
    isolation. Mirrors ``test_memory_provider_run_wiring.py``'s ``MockModel``, extended with a
    reply script: that file's canned single response is not enough for a multi-turn scenario
    where each turn's write-back content must stay distinct (so consolidation's Jaccard grouping
    does not fold turns together that this test needs to stay separate).
    """

    def __init__(self, replies: List[str]):
        super().__init__(id="scripted-model", name="scripted-model", provider="test")
        self._replies = list(replies)
        self._call = 0
        self.invocations: List[List[Any]] = []

    def _next_reply(self) -> str:
        reply = self._replies[min(self._call, len(self._replies) - 1)]
        self._call += 1
        return reply

    def get_instructions_for_model(self, *args, **kwargs):
        return None

    def get_system_message_for_model(self, *args, **kwargs):
        return None

    async def aget_instructions_for_model(self, *args, **kwargs):
        return None

    async def aget_system_message_for_model(self, *args, **kwargs):
        return None

    def parse_args(self, *args, **kwargs):
        return {}

    def _response(self) -> ModelResponse:
        return ModelResponse(content=self._next_reply(), role="assistant", response_usage=MessageMetrics())

    def invoke(self, *args, **kwargs) -> ModelResponse:
        self.invocations.append(kwargs.get("messages", []))
        return self._response()

    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        self.invocations.append(kwargs.get("messages", []))
        return self._response()

    def invoke_stream(self, *args, **kwargs) -> Iterator[ModelResponse]:
        self.invocations.append(kwargs.get("messages", []))
        yield self._response()

    async def ainvoke_stream(self, *args, **kwargs) -> AsyncIterator[ModelResponse]:
        self.invocations.append(kwargs.get("messages", []))
        yield self._response()

    def _parse_provider_response(self, response: Any, **kwargs) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


def _pg_url() -> str:
    return os.getenv("EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai")


def _neo4j_settings() -> dict:
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
    }


def _config(mode: ProviderMode) -> MemoryProviderConfig:
    """One config per mode, both legs real. The only construction call site is
    ``build_memory_provider`` -- nothing here names ``CoLocatedMemoryProvider``/
    ``ExternalMemoryProvider`` directly. ``provider`` follows ``mode`` (SM-X2 #138): Mode A
    (``LOCAL``) resolves ``"co_located"``, Mode B (``FRONTIER``) resolves ``"external"`` -- the
    same pairing a real per-tenant config (SM-C5 #93) would carry, rather than one adapter wearing
    both modes."""
    return MemoryProviderConfig(
        provider="external" if mode is ProviderMode.FRONTIER else "co_located",
        mode=mode,
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


def _fresh_provider(mode: ProviderMode, tenant_id: str) -> CoLocatedMemoryProvider:
    """Build a provider for ``mode``, clearing any rows ``tenant_id`` left behind from a previous
    run -- cleared before returning, not at teardown, so one test's leftovers cannot leak into the
    next (the same drop-before-yield split ``test_mode_a.py``'s ``provider`` fixture uses).

    Scoped to the exact ``tenant_id`` in play, not a shared prefix: this file runs four
    parametrized cases (``mode_a``/``mode_b`` x sync/async) whose tenants share a common prefix
    (``acme-local``/``acme-frontier``, ``globex-local``/``globex-frontier``) -- a prefix-scoped
    delete can't tell those apart and would let one case's reset clobber another's in-flight rows
    under concurrent execution (e.g. ``-n``/xdist). Exact-match scoping also means a run here can't
    clobber ``test_mode_a.py``'s graph state, since that file's tenants are plain ``"acme"``/
    ``"globex"``, never equal to this file's ``mode``-suffixed ids.
    """
    built = build_memory_provider(_config(mode))
    assert isinstance(built, CoLocatedMemoryProvider)  # true for both: ExternalMemoryProvider subclasses it
    assert isinstance(built, ExternalMemoryProvider) == (mode is ProviderMode.FRONTIER)
    with built.vector._engine.begin() as connection:
        connection.execute(
            text(f"DELETE FROM {sql.qualified('ai', PG_TABLE)} WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
    with built.graph._driver.session(database=built.graph.database) as session:
        session.run("MATCH (n {tenant_id: $tenant_id}) DETACH DELETE n", tenant_id=tenant_id)
    return built


@_MODES
def test_full_loop_write_consolidate_retrieve_inject(mode):
    """The whole symbiotic loop, driven through real ``Agent`` turns, on real backends.

    Turn 1 and turn 2 each produce a distinct user + assistant episode (write-back, SM-E2/SM-X3).
    An explicit ``consolidate`` call (SM-L7) distils them into semantic facts with the deterministic
    zero-config strategy -- the same one ``test_mode_a.py`` exercises, so a mismatch between the
    fake-substrate-era wiring proof and a real distillation pass would show here. Turn 3's
    feed-forward hook then reads real memory back and the assertion is on what the *model actually
    received* -- proving injection, not just that ``provider.read`` can find the fact when called
    directly.
    """
    tenant_id = f"acme-{mode.value}"
    provider = _fresh_provider(mode, tenant_id)
    try:
        user_id = "ada"
        session_id = "s1"
        model = ScriptedModel(
            replies=[
                "Got it, email over phone it is.",
                "Noted, I will CC your manager on invoices.",
                "You said you prefer email over phone for support tickets.",
            ]
        )
        agent = Agent(model=model, memory_provider=provider, telemetry=False)

        def _turn(run_id: str, message: str):
            return agent.run(
                message,
                session_id=session_id,
                user_id=user_id,
                run_context=RunContext(run_id=run_id, session_id=session_id, user_id=user_id, tenant_id=tenant_id),
            )

        # -- Turn 1 & 2: write-back lands two distinct episodes each (user + assistant). -------
        _turn("r1", "I prefer email over phone for support tickets.")
        _turn("r2", "Please always CC my manager on invoices.")

        # -- Consolidate: episodic -> semantic (SM-L7), scoped like a real scheduler pass would. -
        consolidate_scope = Scope(tenant_id=tenant_id, user_id=user_id)
        report = provider.consolidate(consolidate_scope)
        assert report.episodic_scanned == 4, "expected 4 episodes: 2 turns x (user + assistant)"
        # Four distinct sentences share too little vocabulary to cross the 0.8 Jaccard threshold,
        # so each stays its own group -- deterministic, not a range, the same reasoning
        # test_mode_a.py's byte-identical-episode case uses in the opposite direction.
        assert report.duplicates_merged == 0
        assert report.semantic_written == 4

        # -- Turn 3: feed-forward read must inject the consolidated fact before the model call. --
        # The ``[semantic]`` tag (not just the phrase) is the point of this assertion: without it,
        # this would also pass on the raw episodic write alone -- ``MemoryQuery``'s default tiers
        # search episodic and semantic together, and the deterministic strategy copies episode
        # content verbatim, so an un-tagged match can't tell "consolidation ran" from "it didn't
        # need to." This doesn't exercise ranking, though: the candidate pool here (4 episodes + 4
        # facts) stays under ``MemoryQuery``'s default ``limit=10``, so every candidate is returned
        # and rendered regardless of score -- the same reasoning as the 0.8-Jaccard-threshold
        # comment above, spelled out so a future extension of this test (more turns, another
        # consolidation pass) doesn't silently push the pool over the limit and stop testing what
        # this assertion claims to.
        _turn("r3", "What did I say about my support ticket preference?")
        injected = [getattr(m, "content", None) for m in model.invocations[-1]]
        assert any(content and "[semantic]" in content and "email over phone" in content for content in injected), (
            f"consolidated memory was not injected into the model call; saw: {injected}"
        )

        # -- Direct backend checks: the fact actually landed on both engines, not only wherever --
        # -- the fused read happened to favour (mirrors test_mode_a.py's per-engine assertions). --
        vector_hits = provider.vector.search(
            consolidate_scope, MemoryQuery(text="email phone support tickets", tiers=[MemoryTier.SEMANTIC])
        )
        assert vector_hits, "the consolidated fact never reached pgvector"

        with provider.graph._driver.session(database=provider.graph.database) as session:
            count = session.run(
                "MATCH (f:Fact {tenant_id: $tenant_id}) RETURN count(f) AS n", tenant_id=tenant_id
            ).single()["n"]
        assert count == 4, f"expected 4 distilled facts on the graph leg, got {count}"

        # -- Substrates function report: this test's real-backend claim, spelled out. -----------
        assert provider.substrates() == {Substrate.VECTOR: True, Substrate.GRAPH: True}
        assert provider.config.mode is mode
    finally:
        provider.close()


@_MODES
async def test_full_loop_write_consolidate_retrieve_inject_async(mode):
    """The async twin: ``arun`` -> ``aconsolidate`` -> ``arun`` again, on the same real backends.

    Not the sync test re-run under ``await`` -- a provider whose async hooks quietly delegated to
    a blocking one would pass the sync test and hang an event loop in production, invisibly, until
    something under load noticed (the same reasoning ``test_mode_a.py``'s async round trip gives).
    Kept to one turn before consolidation rather than two: this test's job is proving the async
    path reaches the same real state as the sync path, not re-deriving the fact-count arithmetic
    the sync test above already pins down.
    """
    tenant_id = f"globex-{mode.value}"
    provider = _fresh_provider(mode, tenant_id)
    try:
        user_id = "bob"
        session_id = "s1"
        model = ScriptedModel(
            replies=[
                "Understood, weekly billing summaries it is.",
                "You asked for weekly billing summaries.",
            ]
        )
        agent = Agent(model=model, memory_provider=provider, telemetry=False)

        async def _turn(run_id: str, message: str):
            return await agent.arun(
                message,
                session_id=session_id,
                user_id=user_id,
                run_context=RunContext(run_id=run_id, session_id=session_id, user_id=user_id, tenant_id=tenant_id),
            )

        await _turn("r1", "Send me weekly billing summaries every Monday.")

        consolidate_scope = Scope(tenant_id=tenant_id, user_id=user_id)
        report = await provider.aconsolidate(consolidate_scope)
        assert report.episodic_scanned == 2, "expected 2 episodes: 1 turn x (user + assistant)"
        assert report.semantic_written == 2

        await _turn("r2", "What did I ask you to send me weekly?")
        injected = [getattr(m, "content", None) for m in model.invocations[-1]]
        assert any(
            content and "[semantic]" in content and "weekly billing summaries" in content for content in injected
        ), f"consolidated memory was not injected into the async model call; saw: {injected}"

        vector_hits = await provider.vector.asearch(
            consolidate_scope, MemoryQuery(text="weekly billing summaries", tiers=[MemoryTier.SEMANTIC])
        )
        assert vector_hits, "the consolidated fact never reached pgvector on the async path"
    finally:
        await provider.aclose()
