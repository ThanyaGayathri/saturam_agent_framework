"""``Reranker``'s default ``arerank`` (SM-H3 #133, aicoe-tracker#83 DECISION.md S5 finding).

Before this ticket only ``InfinityReranker`` had an async path; every other adapter --
``cohere``, ``sentence_transformer``, ``aws_bedrock`` -- had none at all. This pins the base
class default (thread-offload of ``rerank``) and that a subclass overriding ``arerank`` -- as
``InfinityReranker`` does -- is used instead of the default.
"""

import threading

from egpt.integrations.knowledge.reranker import base as reranker_base
from egpt.integrations.knowledge.reranker.base import Reranker
from egpt.knowledge.document import Document


class _SyncOnlyReranker(Reranker):
    """Declares no `arerank` of its own -- exercises the base class default."""

    def rerank(self, query, documents):
        return [
            Document(id=doc.id, content=doc.content, reranking_score=float(len(documents) - i))
            for i, doc in enumerate(documents)
        ]


class _AsyncOverrideReranker(Reranker):
    """Declares its own `arerank`, the way `InfinityReranker` does."""

    def rerank(self, query, documents):
        raise AssertionError("sync rerank must not run when arerank is overridden")

    async def arerank(self, query, documents):
        return list(reversed(documents))


async def test_the_base_class_default_arerank_produces_the_same_result_as_rerank():
    documents = [Document(id="a", content="a"), Document(id="b", content="b")]
    reranker = _SyncOnlyReranker()

    sync_result = reranker.rerank("q", documents)
    async_result = await reranker.arerank("q", documents)

    assert [d.id for d in async_result] == [d.id for d in sync_result]


async def test_the_base_class_default_arerank_offloads_to_a_thread():
    calling_thread = threading.current_thread()
    seen_thread = {}

    class _ThreadRecordingReranker(Reranker):
        def rerank(self, query, documents):
            seen_thread["thread"] = threading.current_thread()
            return documents

    await _ThreadRecordingReranker().arerank("q", [Document(id="a", content="a")])

    assert seen_thread["thread"] is not calling_thread


async def test_a_subclasss_own_arerank_override_is_used_not_the_base_default():
    documents = [Document(id="a", content="a"), Document(id="b", content="b")]

    result = await _AsyncOverrideReranker().arerank("q", documents)

    assert [d.id for d in result] == ["b", "a"]


async def test_the_default_arerank_uses_a_dedicated_bounded_executor_not_the_shared_default():
    """Round-3 review finding (PR #148): `asyncio.to_thread`'s process-wide default executor is
    shared with every unrelated `to_thread` call in the app -- a slow/hung rerank call (this
    default's caller, `egpt.memory.retrieval.rerank`, budgets and abandons overruns rather than
    waiting) would otherwise pin a slot there for as long as the call keeps running. Spies on
    `_ARERANK_EXECUTOR.submit` itself (which `loop.run_in_executor` calls under the hood) rather
    than just checking the module attribute is untouched, since routing through
    `asyncio.to_thread` instead would leave that identity unchanged too."""
    real_submit = reranker_base._ARERANK_EXECUTOR.submit
    calls = []

    def spy_submit(*args, **kwargs):
        calls.append(1)
        return real_submit(*args, **kwargs)

    reranker_base._ARERANK_EXECUTOR.submit = spy_submit
    try:
        await _SyncOnlyReranker().arerank("q", [Document(id="a", content="a")])
        await _SyncOnlyReranker().arerank("q", [Document(id="a", content="a")])
    finally:
        reranker_base._ARERANK_EXECUTOR.submit = real_submit

    assert len(calls) == 2
    assert len(reranker_base._ARERANK_EXECUTOR._threads) == 4


def test_the_arerank_executors_workers_are_daemon_threads():
    """Round-4 review finding: non-daemon worker threads block graceful process exit (CPython's
    `atexit` hook joins them, abandoned/in-flight work included). `_ARERANK_EXECUTOR`'s own type
    (`BoundedDaemonExecutor`) guarantees this; `egpt.utils.concurrency`'s own tests cover the
    mechanism in depth -- this just pins that the module-level singleton this default actually
    uses is one."""
    assert all(t.daemon for t in reranker_base._ARERANK_EXECUTOR._threads)
