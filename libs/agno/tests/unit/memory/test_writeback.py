"""Write-back capture: run artifacts -> episodes -> background enqueue (SM-E2 #109).

Every assertion here is on the two things the acceptance criteria actually name: what an episode
looks like once captured, and that enqueuing one never makes the caller wait for the write. What
SM-L1's consolidation loop later does with a written episode, and what SM-X3 wires this module
into, are both out of scope -- there is nothing to assert about a run loop or a scheduler this
module does not have.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.schemas.episodic import episode_from_event
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import PartialWriteError, Scope
from egpt.memory.writeback import (
    ROLE_KEY,
    TOOL_CALL_ID_KEY,
    TOOL_NAME_KEY,
    event_from_message,
    event_from_tool_execution,
    events_from_run,
    start_writeback_future,
    start_writeback_task,
)
from egpt.models.message import Message
from egpt.models.response import ToolExecution
from tests.unit.memory._fakes import FakeGraph, FakeVector

SCOPE = Scope(tenant_id="acme", user_id="ada")


def provider():
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config), vector, graph


# ---------------------------------------------------------------------------
# Builders: run artifacts -> episodic MemoryEvent
# ---------------------------------------------------------------------------


def test_event_from_message_is_episodic_and_carries_role():
    message = Message(role="user", content="deploy the memory service")
    event = event_from_message(message, SCOPE)

    assert event is not None
    assert event.tier is MemoryTier.EPISODIC
    assert event.content == "deploy the memory service"
    assert event.scope == SCOPE
    assert event.role == "user"
    assert event.metadata[ROLE_KEY] == "user"


@pytest.mark.parametrize("content", [None, "", "   "])
def test_event_from_message_is_none_for_content_free_messages(content):
    message = Message(role="assistant", content=content)
    assert event_from_message(message, SCOPE) is None


def test_event_from_message_with_list_content_is_not_treated_as_blank():
    """`Message.get_content_string()` serialises a non-empty `list` content (a tool-call-only
    assistant turn, or a vision payload) as JSON rather than an empty string, so it is not
    "content-free" the way `None` / `""` / whitespace are. Documents the chosen behaviour: such a
    message becomes an episode whose content is the JSON-encoded structure verbatim -- segmenting
    or summarising that content is not this ticket's job (SM-E3 #110)."""
    message = Message(role="assistant", content=[{"type": "tool_use", "id": "t1", "name": "search"}])
    event = event_from_message(message, SCOPE)

    assert event is not None
    assert event.content == message.get_content_string()
    assert event.content != ""


def test_event_from_tool_execution_is_episodic_and_carries_tool_identity():
    execution = ToolExecution(tool_call_id="call-1", tool_name="search", result="3 hits")
    event = event_from_tool_execution(execution, SCOPE)

    assert event is not None
    assert event.tier is MemoryTier.EPISODIC
    assert event.content == "3 hits"
    assert event.metadata[TOOL_NAME_KEY] == "search"
    assert event.metadata[TOOL_CALL_ID_KEY] == "call-1"


@pytest.mark.parametrize(
    ("tool_name", "tool_call_id"),
    [("search", None), (None, "call-1")],
    ids=["tool_call_id_only_missing", "tool_name_only_missing"],
)
def test_event_from_tool_execution_carries_whichever_identifier_is_set(tool_name, tool_call_id):
    """`event_from_tool_execution` guards `tool_name` and `tool_call_id` independently -- neither
    is required for the other, so each of the two metadata keys must appear or be absent on its
    own, not as a pair."""
    execution = ToolExecution(tool_call_id=tool_call_id, tool_name=tool_name, result="ok")
    event = event_from_tool_execution(execution, SCOPE)

    assert event is not None
    if tool_name is None:
        assert TOOL_NAME_KEY not in event.metadata
    else:
        assert event.metadata[TOOL_NAME_KEY] == tool_name
    if tool_call_id is None:
        assert TOOL_CALL_ID_KEY not in event.metadata
    else:
        assert event.metadata[TOOL_CALL_ID_KEY] == tool_call_id


@pytest.mark.parametrize("result", [None, "", "  "])
def test_event_from_tool_execution_is_none_without_a_result(result):
    """Covers the error / paused / not-yet-returned states, which all leave `result` unset --
    there is nothing yet to remember as an episode, whichever of those it is."""
    execution = ToolExecution(tool_call_id="call-1", tool_name="search", result=result)
    assert event_from_tool_execution(execution, SCOPE) is None


def test_writeback_metadata_does_not_collide_with_the_episode_projection():
    """`episode_from_event` (SM-E1) refuses caller metadata that collides with a reserved episode
    key. If ROLE_KEY or TOOL_NAME_KEY / TOOL_CALL_ID_KEY ever drifted onto one of those, every
    write-back event would fail at the seam instead of here -- this pins the two modules staying
    disjoint."""
    message_event = event_from_message(Message(role="user", content="hi"), SCOPE, occurred_at=1_700_000_000)
    assert message_event is not None
    episode_from_event(replace(message_event, id="ep-msg"))  # must not raise

    tool_event = event_from_tool_execution(
        ToolExecution(tool_call_id="c1", tool_name="search", result="ok"), SCOPE, occurred_at=1_700_000_000
    )
    assert tool_event is not None
    episode_from_event(replace(tool_event, id="ep-tool"))  # must not raise


def test_events_from_run_orders_messages_before_tool_executions_and_drops_blanks():
    messages = [
        Message(role="user", content="find the deploy doc"),
        Message(role="assistant", content=""),  # dropped
        Message(role="assistant", content="looking it up"),
    ]
    tool_executions = [
        ToolExecution(tool_call_id="c1", tool_name="search", result=None),  # dropped
        ToolExecution(tool_call_id="c2", tool_name="search", result="found it"),
    ]

    events = events_from_run(SCOPE, messages=messages, tool_executions=tool_executions)

    assert [e.content for e in events] == ["find the deploy doc", "looking it up", "found it"]
    assert all(e.tier is MemoryTier.EPISODIC for e in events)


def test_events_from_run_forwards_occurred_at_topics_and_metadata_to_every_event():
    events = events_from_run(
        SCOPE,
        messages=[Message(role="user", content="deploy")],
        tool_executions=[ToolExecution(tool_call_id="c1", tool_name="search", result="found it")],
        occurred_at=1_700_000_000,
        topics=["deploy"],
        metadata={"channel": "slack"},
    )

    assert len(events) == 2
    for event in events:
        assert event.occurred_at == 1_700_000_000
        assert event.topics == ["deploy"]
        assert event.metadata["channel"] == "slack"


def test_events_from_run_is_empty_when_nothing_was_captured():
    assert events_from_run(SCOPE) == []
    assert events_from_run(SCOPE, messages=[Message(role="user", content="")]) == []


# ---------------------------------------------------------------------------
# Dispatch: enqueue off the request path
# ---------------------------------------------------------------------------


def test_start_writeback_future_returns_none_for_an_empty_batch():
    memory_provider, _, _ = provider()
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert start_writeback_future(memory_provider, SCOPE, [], executor=executor) is None


async def test_start_writeback_task_returns_none_for_an_empty_batch():
    memory_provider, _, _ = provider()
    assert start_writeback_task(memory_provider, SCOPE, []) is None


def test_start_writeback_future_does_not_block_the_caller():
    """The regression this pins: a caller synchronously invoking `provider.write` would block for
    as long as the substrate takes. Mutate `start_writeback_future` to call `_write_and_log`
    directly instead of `executor.submit(...)` and this fails -- the elapsed time on the calling
    thread crosses the artificial delay below."""
    memory_provider, vector, _ = provider()
    real_upsert = vector.upsert

    def slow_upsert(scope, records):
        time.sleep(0.2)
        return real_upsert(scope, records)

    vector.upsert = slow_upsert
    events = events_from_run(SCOPE, messages=[Message(role="user", content="slow write")])

    with ThreadPoolExecutor(max_workers=1) as executor:
        started = time.monotonic()
        future = start_writeback_future(memory_provider, SCOPE, events, executor=executor)
        elapsed = time.monotonic() - started

        assert elapsed < 0.1, f"start_writeback_future blocked the caller for {elapsed:.3f}s"
        assert future is not None
        receipt = future.result(timeout=2)
        assert receipt.accepted == 1


async def test_start_writeback_task_does_not_block_before_the_next_await():
    """The async-side regression: a caller awaiting `provider.awrite` directly would suspend on it
    immediately. `create_task` schedules the coroutine without running it, so nothing is written
    until this test itself yields control -- checked below before and after that yield."""
    memory_provider, vector, _ = provider()
    events = events_from_run(SCOPE, messages=[Message(role="user", content="async write")])

    task = start_writeback_task(memory_provider, SCOPE, events)
    assert task is not None
    assert vector.rows == {}, "the write ran before the caller yielded control"

    receipt = await task
    assert receipt.accepted == 1
    assert len(vector.rows["acme"]) == 1


def test_start_writeback_future_surfaces_a_failed_write_rather_than_swallowing_it():
    memory_provider, vector, _ = provider()

    def failing_upsert(scope, records):
        raise RuntimeError("substrate unavailable")

    vector.upsert = failing_upsert
    events = events_from_run(SCOPE, messages=[Message(role="user", content="will fail")])

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = start_writeback_future(memory_provider, SCOPE, events, executor=executor)
        assert future is not None
        with pytest.raises(PartialWriteError) as excinfo:
            future.result(timeout=2)
        assert excinfo.value.receipt.accepted == 1


async def test_start_writeback_task_surfaces_a_failed_write_rather_than_swallowing_it():
    memory_provider, vector, _ = provider()

    async def failing_aupsert(scope, records):
        raise RuntimeError("substrate unavailable")

    vector.aupsert = failing_aupsert
    events = events_from_run(SCOPE, messages=[Message(role="user", content="will fail")])

    task = start_writeback_task(memory_provider, SCOPE, events)
    assert task is not None
    with pytest.raises(PartialWriteError) as excinfo:
        await task
    assert excinfo.value.receipt.accepted == 1
