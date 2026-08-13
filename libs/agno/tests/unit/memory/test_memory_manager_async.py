from types import MethodType
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import pytest

from egpt.db.base import AsyncBaseDb
from egpt.memory import MemoryManager, UserMemory

if TYPE_CHECKING:
    from egpt.tracing.schemas import Span, Trace


class DummyAsyncMemoryDb(AsyncBaseDb):
    def __init__(self):
        super().__init__()
        self.calls: List[Tuple[str, Optional[str]]] = []
        self._memories: Dict[str, Dict[str, UserMemory]] = {}

    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists - dummy implementation always returns True."""
        return True

    async def delete_session(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_sessions(self, *args, **kwargs):
        raise NotImplementedError

    async def get_session(self, *args, **kwargs):
        raise NotImplementedError

    async def get_sessions(self, *args, **kwargs):
        raise NotImplementedError

    async def rename_session(self, *args, **kwargs):
        raise NotImplementedError

    async def upsert_session(self, *args, **kwargs):
        raise NotImplementedError

    async def clear_memories(self):
        self._memories.clear()

    async def delete_user_memory(self, memory_id: str, user_id: Optional[str] = None) -> None:
        user = user_id or "default"
        self._memories.get(user, {}).pop(memory_id, None)

    async def delete_user_memories(self, memory_ids, user_id: Optional[str] = None) -> None:
        user = user_id or "default"
        user_memories = self._memories.get(user, {})
        for memory_id in memory_ids:
            user_memories.pop(memory_id, None)

    async def get_all_memory_topics(self, *args, **kwargs) -> List[str]:
        topics = set()
        for memories in self._memories.values():
            for memory in memories.values():
                if memory.topics:
                    topics.update(memory.topics)
        return sorted(topics)

    async def get_user_memory(self, memory_id: str, deserialize: Optional[bool] = True, user_id: Optional[str] = None):
        user = user_id or "default"
        memory = self._memories.get(user, {}).get(memory_id)
        if memory is None:
            return None
        return memory if deserialize else memory.to_dict()

    async def get_user_memories(
        self,
        user_id: Optional[str] = None,
        *args,
        **kwargs,
    ):
        self.calls.append(("get_user_memories", user_id))
        if user_id is None:
            return [memory for memories in self._memories.values() for memory in memories.values()]
        return list(self._memories.get(user_id, {}).values())

    async def get_user_memory_stats(self, *args, **kwargs) -> Tuple[List[Dict], int]:
        return [], 0

    async def upsert_user_memory(self, memory: UserMemory, deserialize: Optional[bool] = True):
        user_id = memory.user_id or "default"
        user_memories = self._memories.setdefault(user_id, {})
        memory_id = memory.memory_id or f"mem-{len(user_memories) + 1}"
        memory.memory_id = memory_id
        user_memories[memory_id] = memory
        return memory if deserialize else memory.to_dict()

    async def get_metrics(self, *args, **kwargs):
        raise NotImplementedError

    async def calculate_metrics(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_knowledge_content(self, *args, **kwargs):
        raise NotImplementedError

    async def get_knowledge_content(self, *args, **kwargs):
        raise NotImplementedError

    async def get_knowledge_contents(self, *args, **kwargs):
        raise NotImplementedError

    async def upsert_knowledge_content(self, *args, **kwargs):
        raise NotImplementedError

    async def create_eval_run(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_eval_runs(self, *args, **kwargs):
        raise NotImplementedError

    async def get_eval_run(self, *args, **kwargs):
        raise NotImplementedError

    async def get_eval_runs(self, *args, **kwargs):
        raise NotImplementedError

    async def rename_eval_run(self, *args, **kwargs):
        raise NotImplementedError

    async def clear_cultural_knowledge(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_cultural_knowledge(self, *args, **kwargs):
        raise NotImplementedError

    async def get_cultural_knowledge(self, *args, **kwargs):
        raise NotImplementedError

    async def get_all_cultural_knowledge(self, *args, **kwargs):
        raise NotImplementedError

    async def upsert_cultural_knowledge(self, *args, **kwargs):
        raise NotImplementedError

    # --- Traces ---
    async def upsert_trace(self, trace: "Trace") -> None:
        raise NotImplementedError

    async def get_trace(self, trace_id: str):
        raise NotImplementedError

    async def get_traces(
        self,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = 100,
    ) -> List:
        raise NotImplementedError

    async def get_trace_stats(self, *args, **kwargs):
        raise NotImplementedError

    # --- Spans ---
    async def create_span(self, span: "Span") -> None:
        raise NotImplementedError

    async def create_spans(self, spans: List) -> None:
        raise NotImplementedError

    async def get_span(self, span_id: str):
        raise NotImplementedError

    async def get_spans(
        self,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        limit: Optional[int] = 1000,
    ) -> List:
        raise NotImplementedError

    # -----------

    async def get_latest_schema_version(self, *args, **kwargs):
        raise NotImplementedError

    async def upsert_schema_version(self, *args, **kwargs):
        raise NotImplementedError

    # --- Learnings ---
    async def get_learning(self, *args, **kwargs):
        raise NotImplementedError

    async def upsert_learning(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_learning(self, *args, **kwargs):
        raise NotImplementedError

    async def get_learnings(self, *args, **kwargs):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_acreate_user_memories_with_async_db():
    async_db = DummyAsyncMemoryDb()
    manager = MemoryManager(db=async_db)

    async def fake_acreate_or_update_memories(
        self,
        *,
        messages,
        existing_memories,
        user_id,
        agent_id,
        team_id,
        db,
        update_memories,
        add_memories,
        run_response=None,
        run_metrics=None,
    ):
        await db.upsert_user_memory(
            UserMemory(
                memory=f"Stored: {messages[0].get_content_string()}",
                user_id=user_id,
                memory_id="mem-1",
            )
        )
        return "ok"

    manager.acreate_or_update_memories = MethodType(fake_acreate_or_update_memories, manager)

    result = await manager.acreate_user_memories(message="Remember the milk", user_id="user-1")

    assert result == "ok"
    assert async_db.calls[:2] == [("get_user_memories", "user-1"), ("get_user_memories", "user-1")]

    user_memories = await manager.aget_user_memories(user_id="user-1")
    assert len(user_memories) == 1
    assert user_memories[0].memory.startswith("Stored:")


@pytest.mark.asyncio
async def test_aupdate_memory_task_refreshes_async_db():
    async_db = DummyAsyncMemoryDb()
    manager = MemoryManager(db=async_db)

    async def fake_arun_memory_task(
        self,
        *,
        task,
        existing_memories,
        user_id,
        db,
        delete_memories,
        update_memories,
        add_memories,
        clear_memories,
    ):
        await db.upsert_user_memory(
            UserMemory(
                memory=f"Task: {task}",
                user_id=user_id,
                memory_id="task-1",
            )
        )
        return "updated"

    manager.arun_memory_task = MethodType(fake_arun_memory_task, manager)

    response = await manager.aupdate_memory_task(task="Sync state", user_id="user-2")

    assert response == "updated"
    assert async_db.calls[:2] == [("get_user_memories", "user-2"), ("get_user_memories", "user-2")]

    saved_memories = await manager.aget_user_memories(user_id="user-2")
    assert len(saved_memories) == 1
    assert saved_memories[0].memory == "Task: Sync state"


# ---------------------------------------------------------------------------
# The two verbs that had no async half until SM-C2 (#90) -- SPEC section 11.3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aadd_user_memory_awaits_an_async_db():
    """`add_user_memory` drives a sync db, so an async deployment paid a worker thread per write."""
    async_db = DummyAsyncMemoryDb()
    manager = MemoryManager(db=async_db)

    memory_id = await manager.aadd_user_memory(UserMemory(memory="likes email"), user_id="user-1")

    assert memory_id is not None
    stored = await manager.aget_user_memories(user_id="user-1")
    assert [m.memory for m in stored] == ["likes email"]


@pytest.mark.asyncio
async def test_aadd_user_memory_also_drives_a_sync_db():
    """Mirrors `aclear_user_memories`: the async half is about the *caller's* lane, and a sync db
    behind it still has to work rather than awaiting a method that returns no coroutine."""
    from unittest.mock import MagicMock

    sync_db = MagicMock()
    sync_db.upsert_user_memory = MagicMock(return_value=None)
    manager = MemoryManager(db=sync_db)

    memory_id = await manager.aadd_user_memory(UserMemory(memory="likes email"), user_id="user-1")

    assert memory_id is not None
    assert sync_db.upsert_user_memory.call_args.kwargs["memory"].user_id == "user-1"


@pytest.mark.asyncio
async def test_both_add_halves_stamp_the_row_identically():
    """Id, owner and timestamp come from one shared `_stamp_user_memory`, so a caller reading the
    row back cannot tell which lane wrote it -- the drift a hand-copied async half invites."""
    sync_manager = MemoryManager(db=DummyAsyncMemoryDb())
    async_manager = MemoryManager(db=DummyAsyncMemoryDb())

    sync_row = UserMemory(memory="likes email", memory_id="fixed")
    async_row = UserMemory(memory="likes email", memory_id="fixed")
    sync_manager._stamp_user_memory(sync_row, user_id="user-1")
    await async_manager.aadd_user_memory(async_row, user_id="user-1")

    assert (sync_row.memory_id, sync_row.user_id) == (async_row.memory_id, async_row.user_id)


@pytest.mark.asyncio
async def test_aadd_user_memory_without_a_db_reports_nothing_stored():
    """Same answer as the sync half: no db, no id -- not a silent success."""
    assert await MemoryManager().aadd_user_memory(UserMemory(memory="x")) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method,expected", [("last_n", ["newest"]), ("first_n", ["oldest"]), (None, ["newest"])])
async def test_asearch_user_memories_selects_the_same_end_the_sync_half_does(method, expected):
    """Under a `limit`, because without one every method returns the whole set and the parameters
    are indistinguishable -- a test that passes for `first_n` and `last_n` alike has not checked
    either. `None` is here too: the default is `last_n` on both halves."""
    manager = MemoryManager(db=DummyAsyncMemoryDb())
    await manager.aadd_user_memory(UserMemory(memory="oldest", memory_id="m1", updated_at=100), user_id="u")
    await manager.aadd_user_memory(UserMemory(memory="newest", memory_id="m2", updated_at=200), user_id="u")

    found = await manager.asearch_user_memories(retrieval_method=method, limit=1, user_id="u")

    assert [m.memory for m in found] == expected


@pytest.mark.asyncio
async def test_asearch_user_memories_honours_the_limit_it_was_given():
    """`limit` reaches the shared selector, not just the fetch -- a result-only check on an
    unlimited store cannot tell those apart."""
    manager = MemoryManager(db=DummyAsyncMemoryDb())
    for index in range(5):
        await manager.aadd_user_memory(UserMemory(memory=f"n{index}", memory_id=f"m{index}", updated_at=index), "u")

    assert len(await manager.asearch_user_memories(retrieval_method="last_n", limit=2, user_id="u")) == 2


@pytest.mark.asyncio
async def test_asearch_user_memories_refuses_an_agentic_search_with_no_query():
    """The sync half raises here too; an async half that returned `[]` would read as "no matches"."""
    manager = MemoryManager(db=DummyAsyncMemoryDb())
    await manager.aadd_user_memory(UserMemory(memory="x", memory_id="m1"), user_id="u")

    with pytest.raises(ValueError, match="Query is required"):
        await manager.asearch_user_memories(retrieval_method="agentic", user_id="u")


@pytest.mark.asyncio
async def test_an_agentic_search_calls_the_model_on_the_async_lane():
    """The one thing that genuinely differs between the halves: `aresponse` against `response`.

    A half that called the sync `response` would block the event loop for the whole round trip,
    which is the failure the async variant exists to prevent and is invisible in the result.
    """

    class RecordingModel:
        supports_native_structured_outputs = False
        supports_json_schema_outputs = False

        def __init__(self):
            self.sync_calls = 0
            self.async_calls = 0

        def response(self, messages, response_format=None):
            self.sync_calls += 1
            raise AssertionError("the async lane must not call the sync model path")

        async def aresponse(self, messages, response_format=None):
            self.async_calls += 1

            class Response:
                content = '{"memory_ids": ["m2"]}'
                parsed = None

            return Response()

    model = RecordingModel()
    manager = MemoryManager(db=DummyAsyncMemoryDb())
    # Assigned after construction: `__init__` runs `get_model`, which admits a `Model` or a string
    # and nothing else, and this double is deliberately neither.
    manager.model = model
    await manager.aadd_user_memory(UserMemory(memory="first", memory_id="m1"), user_id="u")
    await manager.aadd_user_memory(UserMemory(memory="second", memory_id="m2"), user_id="u")

    found = await manager.asearch_user_memories(query="second", retrieval_method="agentic", user_id="u")

    assert (model.async_calls, model.sync_calls) == (1, 0)
    assert [m.memory for m in found] == ["second"]
