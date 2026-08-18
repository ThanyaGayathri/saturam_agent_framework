"""Write-back capture: run artifacts -> episodes -> background enqueue (SM-E2 #109).

The write side of the symbiotic loop (ADR 0003): a turn produces messages, tool results, and
other events, and this module turns them into :class:`~egpt.memory.types.MemoryEvent` s and hands
them to :meth:`~egpt.memory.provider.MemoryProvider.write` / ``awrite`` off the request path.

Two halves, matching the seam's own append/consolidate split:

* :func:`event_from_message` / :func:`event_from_tool_execution` build **episodic** events. Pure
  construction -- no I/O, no model call -- so there is one sync form each, per CLAUDE.md's
  exemption for methods with nothing to await.
* :func:`start_writeback_future` / :func:`start_writeback_task` dispatch the write. ``write`` /
  ``awrite`` reach a real substrate (SM-C1's docstring calls it "cheap", not "free"), so calling
  either inline still blocks the turn on network I/O. These mirror the
  ``start_memory_future`` / ``astart_memory_task`` pair in :mod:`egpt.agent._managers` --
  ``ThreadPoolExecutor.submit`` for a sync run, ``asyncio.create_task`` for an async one -- so a
  caller gets a handle back immediately and the write lands in the background.

**Wiring this into an actual agent turn is SM-X3's (#139).** This module is deliberately
importable and testable without ``agent/_run.py``: SM-X3 owns calling these functions from the
run loop and choosing which executor to pass. Nothing here reads ``Agent`` or ``RunMessages``.

**Not built here:** inline segmentation (time-gap / turn-count boundaries into distinct episodes)
is SM-E3's (#110) -- ADR 0003 lists it under write-back but as its own ticket, and one event in is
one event out here. Scoring ``importance`` is SM-E5's (#112); the events this module builds carry
the schema default. Draining the resulting queue -- i.e. actually running ``consolidate`` on a
schedule -- is SM-L1's (#122); this module only gets episodes durably written, which is what a
future consolidation pass reads.
"""

from asyncio import Task, create_task
from concurrent.futures import Executor, Future
from typing import Any, Dict, List, Optional, Sequence

from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, Scope, WriteReceipt
from egpt.models.message import Message
from egpt.models.response import ToolExecution
from egpt.utils.log import log_debug, log_warning

__all__ = [
    "ROLE_KEY",
    "TOOL_CALL_ERROR_KEY",
    "TOOL_CALL_ID_KEY",
    "TOOL_NAME_KEY",
    "event_from_message",
    "event_from_tool_execution",
    "events_from_run",
    "start_writeback_future",
    "start_writeback_task",
]

#: Where :func:`event_from_message` puts ``Message.role`` *in addition to* the typed
#: ``MemoryEvent.role`` field -- distinct slots. ``MemoryEvent.role`` is what a provider reads
#: directly (``adapters/seed.py`` does: ``event.role or "user"``), but ``episode_from_event``
#: (SM-E1) deliberately does not carry that typed field into the stored episode -- its docstring
#: names this module as the one caller with a role to place once the event reaches that
#: projection. Not one of ``egpt.memory.schemas.episodic.EPISODE_KEYS`` / ``TOPICS_KEY``, so it
#: survives that projection as ordinary metadata.
ROLE_KEY = "role"

#: Where :func:`event_from_tool_execution` puts ``ToolExecution.tool_name`` / ``tool_call_id``.
#: A tool result has no ``role`` to carry -- it is not a message -- so it gets its own pair of
#: metadata keys rather than overloading :data:`ROLE_KEY` with a non-role value.
TOOL_NAME_KEY = "tool_name"
TOOL_CALL_ID_KEY = "tool_call_id"

#: Set to ``True`` in metadata when :func:`event_from_tool_execution` captures a tool call that
#: errored. Write-back does not filter errored tool results out -- ADR 0003 requires ``write`` to
#: be non-lossy, and a tool call failing is itself something that happened in the turn, worth
#: remembering as an episode (e.g. "search API rate limited") rather than dropped. This key is
#: what lets a reader distinguish that episode's content from an ordinary successful result,
#: since ``ToolExecution.result`` carries the error message in both cases (``models/base.py``:
#: ``content=output if success else function_call.error``).
TOOL_CALL_ERROR_KEY = "tool_call_error"


def _content_or_none(value: Optional[str]) -> Optional[str]:
    """Empty and whitespace-only content are both "nothing happened", the same way
    ``agent/_managers.py`` already filters ``run_messages.extra_messages`` before building
    memories. Checked once here rather than by every caller of the two builders below.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def event_from_message(
    message: Message,
    scope: Scope,
    *,
    occurred_at: Optional[int] = None,
    topics: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[MemoryEvent]:
    """Build an episodic event from one run message, or ``None`` for a content-free message.

    ``None`` rather than raising: a tool-call-only assistant message or a system message with no
    text is routine, and a caller batching many messages (see :func:`events_from_run`) wants to
    drop those, not stop on them.

    ``occurred_at`` is epoch seconds, matching ``MemoryEvent.occurred_at`` -- a caller holding a
    ``datetime`` converts with :func:`egpt.utils.dttm.to_epoch_s` first.

    Caller ``metadata`` carrying :data:`ROLE_KEY` is overwritten unconditionally by
    ``message.role``: the message's own role is authoritative here, not whatever a caller happened
    to put under the same key.
    """
    content = _content_or_none(message.get_content_string())
    if content is None:
        return None
    event_metadata: Dict[str, Any] = dict(metadata or {})
    event_metadata[ROLE_KEY] = message.role
    return MemoryEvent(
        content=content,
        scope=scope,
        tier=MemoryTier.EPISODIC,
        role=message.role,
        topics=list(topics) if topics is not None else None,
        occurred_at=occurred_at,
        metadata=event_metadata,
    )


def event_from_tool_execution(
    execution: ToolExecution,
    scope: Scope,
    *,
    occurred_at: Optional[int] = None,
    topics: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[MemoryEvent]:
    """Build an episodic event from one tool result, or ``None`` if it carries no result text.

    A tool call that was paused for confirmation or has not returned yet has nothing worth
    remembering as an episode -- ``execution.result`` is ``None`` in both cases, so the same
    content-free check :func:`event_from_message` uses covers them. An *errored* call is
    different: ``result`` legitimately carries the error message (``models/base.py`` sets
    ``content=... if success else function_call.error``), and write-back does not drop it -- ADR
    0003 requires ``write`` to be non-lossy, and "the tool failed" is itself part of the turn.
    :data:`TOOL_CALL_ERROR_KEY` is set so a reader can tell the two apart without re-deriving it
    from the content.

    ``occurred_at`` is epoch seconds, matching ``MemoryEvent.occurred_at`` -- a caller holding a
    ``datetime`` converts with :func:`egpt.utils.dttm.to_epoch_s` first.

    Caller ``metadata`` carrying :data:`TOOL_NAME_KEY` / :data:`TOOL_CALL_ID_KEY` /
    :data:`TOOL_CALL_ERROR_KEY` is overwritten unconditionally by the execution's own fields, for
    the same reason :func:`event_from_message` overwrites :data:`ROLE_KEY`.
    """
    content = _content_or_none(execution.result)
    if content is None:
        return None
    event_metadata: Dict[str, Any] = dict(metadata or {})
    if execution.tool_name is not None:
        event_metadata[TOOL_NAME_KEY] = execution.tool_name
    if execution.tool_call_id is not None:
        event_metadata[TOOL_CALL_ID_KEY] = execution.tool_call_id
    if execution.tool_call_error:
        event_metadata[TOOL_CALL_ERROR_KEY] = True
    return MemoryEvent(
        content=content,
        scope=scope,
        tier=MemoryTier.EPISODIC,
        topics=list(topics) if topics is not None else None,
        occurred_at=occurred_at,
        metadata=event_metadata,
    )


def events_from_run(
    scope: Scope,
    *,
    messages: Sequence[Message] = (),
    tool_executions: Sequence[ToolExecution] = (),
    occurred_at: Optional[int] = None,
    topics: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[MemoryEvent]:
    """Build every episodic event one turn produces, content-free ones already dropped.

    Order is messages first, then tool executions, both in the order given -- the order a
    consolidation pass would want to read a turn back in, and stable so two calls over the same
    turn produce the same sequence.

    ``metadata``, like ``occurred_at`` and ``topics``, is forwarded to every event in the batch --
    a caller with a run-level annotation (e.g. a channel id) applies it once here rather than to
    each builder call individually. Each builder still owns writing its own reserved keys
    (:data:`ROLE_KEY`, :data:`TOOL_NAME_KEY`, :data:`TOOL_CALL_ID_KEY`) into the same dict.
    """
    events: List[MemoryEvent] = []
    for message in messages:
        event = event_from_message(message, scope, occurred_at=occurred_at, topics=topics, metadata=metadata)
        if event is not None:
            events.append(event)
    for execution in tool_executions:
        event = event_from_tool_execution(execution, scope, occurred_at=occurred_at, topics=topics, metadata=metadata)
        if event is not None:
            events.append(event)
    return events


def _write_and_log(provider: MemoryProvider, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
    """The body run in the background thread.

    Logs at the point of failure and re-raises into the ``Future`` -- a caller who checks
    ``Future.result()`` still sees the exception, and the log fires even if they never do, which
    is what keeps a background write failure from being a silently dropped episode.
    """
    try:
        return provider.write(scope, events)
    except Exception as exc:
        log_warning(f"Write-back capture failed for scope {scope.key()}: {exc}")
        raise


async def _awrite_and_log(provider: MemoryProvider, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
    """Async twin of :func:`_write_and_log`. An ``asyncio.Task`` whose exception is never retrieved
    only logs "Task exception was never retrieved" well after the fact, if at all -- this logs the
    failure at the point it happens, with the scope that lost the episode.
    """
    try:
        return await provider.awrite(scope, events)
    except Exception as exc:
        log_warning(f"Write-back capture failed for scope {scope.key()}: {exc}")
        raise


def start_writeback_future(
    provider: MemoryProvider,
    scope: Scope,
    events: Sequence[MemoryEvent],
    *,
    executor: Executor,
) -> Optional[Future[WriteReceipt]]:
    """Enqueue a write-back batch on a background thread; returns immediately.

    ``None`` for an empty batch -- nothing to submit, and a caller checking "did this queue
    anything" gets a real answer instead of a completed future for zero events.

    The sync half of the pair; :func:`start_writeback_task` is the async one. Both exist because
    an agent run is sometimes sync and sometimes async, and calling ``provider.write`` inline on
    either path is the hot-path I/O this ticket's acceptance criteria rule out -- ``write`` reaches
    a real vector/graph substrate (SM-C1), so it is cheap relative to consolidation's LLM calls but
    not free relative to a request.
    """
    if not events:
        return None
    log_debug(f"Enqueuing {len(events)} write-back event(s) for scope {scope.key()}")
    # Defensive copy: the background thread reads `events` at its own pace, after this function
    # has already returned control to a caller who may go on to mutate the sequence it passed in.
    return executor.submit(_write_and_log, provider, scope, list(events))


def start_writeback_task(
    provider: MemoryProvider,
    scope: Scope,
    events: Sequence[MemoryEvent],
) -> Optional[Task[WriteReceipt]]:
    """Async twin of :func:`start_writeback_future`, via ``asyncio.create_task``.

    Requires a running event loop, same as every other ``create_task`` background dispatch in this
    codebase (``agent/_managers.py``, ``agent/_run.py``) -- the caller is already inside one, since
    this is only ever reached from an async run.

    A caller that discards the returned ``Task`` without awaiting it still gets the failure
    logged by :func:`_awrite_and_log` -- but asyncio separately logs "Task exception was never
    retrieved" at garbage-collection time for any task whose exception nobody read. The
    ``add_done_callback`` below reads it first, so a discarded task produces the one meaningful
    log line instead of two.
    """
    if not events:
        return None
    log_debug(f"Enqueuing {len(events)} write-back event(s) for scope {scope.key()}")
    # Defensive copy: see the matching comment in `start_writeback_future`.
    task = create_task(
        _awrite_and_log(provider, scope, list(events)),
        name=f"writeback:{scope.key()}",
    )
    task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
    return task
