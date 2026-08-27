"""Background task orchestration for memory and learning."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import (
    TYPE_CHECKING,
    Optional,
)

if TYPE_CHECKING:
    from egpt.metrics import RunMetrics
    from egpt.run.base import RunContext
    from egpt.run.team import TeamRunOutput
    from egpt.team.team import Team

from typing import List

from egpt.db.base import UserMemory
from egpt.memory.types import MemoryQuery, Scope
from egpt.memory.working import WorkingMemory
from egpt.run.messages import RunMessages
from egpt.session import TeamSession
from egpt.utils.log import log_debug, log_warning

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def _make_memories(
    team: Team,
    run_messages: RunMessages,
    user_id: Optional[str] = None,
) -> Optional[RunMetrics]:
    from egpt.metrics import RunMetrics

    collector = RunMetrics()
    user_message_str = run_messages.user_message.get_content_string() if run_messages.user_message is not None else None
    if (
        user_message_str is not None
        and user_message_str.strip() != ""
        and team.memory_manager is not None
        and team.update_memory_on_run
    ):
        log_debug("Managing user memories")
        team.memory_manager.create_user_memories(
            message=user_message_str,
            user_id=user_id,
            team_id=team.id,
            run_metrics=collector,
        )
    return collector


async def _amake_memories(
    team: Team,
    run_messages: RunMessages,
    user_id: Optional[str] = None,
) -> Optional[RunMetrics]:
    from egpt.metrics import RunMetrics

    collector = RunMetrics()
    user_message_str = run_messages.user_message.get_content_string() if run_messages.user_message is not None else None
    if (
        user_message_str is not None
        and user_message_str.strip() != ""
        and team.memory_manager is not None
        and team.update_memory_on_run
    ):
        log_debug("Managing user memories")
        await team.memory_manager.acreate_user_memories(
            message=user_message_str,
            user_id=user_id,
            team_id=team.id,
            run_metrics=collector,
        )
    return collector


async def _astart_memory_task(
    team: Team,
    run_messages: RunMessages,
    user_id: Optional[str],
    existing_task: Optional[asyncio.Task],
) -> Optional[asyncio.Task]:
    """Cancel any existing memory task and start a new one if conditions are met.

    Args:
        run_messages: The run messages containing the user message.
        user_id: The user ID for memory creation.
        existing_task: An existing memory task to cancel before starting a new one.

    Returns:
        A new memory task if conditions are met, None otherwise.
    """
    # Cancel any existing task from a previous retry attempt
    if existing_task is not None and not existing_task.done():
        existing_task.cancel()
        try:
            await existing_task
        except asyncio.CancelledError:
            pass

    # Create new task if conditions are met
    if (
        run_messages.user_message is not None
        and team.memory_manager is not None
        and team.update_memory_on_run
        and not team.enable_agentic_memory
    ):
        log_debug("Starting memory creation in background task.")
        return asyncio.create_task(_amake_memories(team, run_messages=run_messages, user_id=user_id))

    return None


def _start_memory_future(
    team: Team,
    run_messages: RunMessages,
    user_id: Optional[str],
    existing_future: Optional[Future],
) -> Optional[Future]:
    """Cancel any existing memory future and start a new one if conditions are met.

    Args:
        run_messages: The run messages containing the user message.
        user_id: The user ID for memory creation.
        existing_future: An existing memory future to cancel before starting a new one.

    Returns:
        A new memory future if conditions are met, None otherwise.
    """
    # Cancel any existing future from a previous retry attempt
    if existing_future is not None and not existing_future.done():
        existing_future.cancel()

    # Create new future if conditions are met
    if (
        run_messages.user_message is not None
        and team.memory_manager is not None
        and team.update_memory_on_run
        and not team.enable_agentic_memory
    ):
        log_debug("Starting memory creation in background thread.")
        return team.background_executor.submit(_make_memories, team, run_messages=run_messages, user_id=user_id)

    return None


def get_user_memories(team: "Team", user_id: Optional[str] = None) -> Optional[List[UserMemory]]:
    """Get the user memories for the given user ID.

    Args:
        user_id: The user ID to get the memories for. If not provided, the current cached user ID is used.
    Returns:
        Optional[List[UserMemory]]: The user memories.
    """
    from egpt.team._init import _set_memory_manager

    if team.memory_manager is None:
        _set_memory_manager(team)

    user_id = user_id if user_id is not None else team.user_id
    if user_id is None:
        user_id = "default"

    return team.memory_manager.get_user_memories(user_id=user_id)  # type: ignore


async def aget_user_memories(team: "Team", user_id: Optional[str] = None) -> Optional[List[UserMemory]]:
    """Get the user memories for the given user ID.

    Args:
        user_id: The user ID to get the memories for. If not provided, the current cached user ID is used.
    Returns:
        Optional[List[UserMemory]]: The user memories.
    """
    from egpt.team._init import _set_memory_manager

    if team.memory_manager is None:
        _set_memory_manager(team)

    user_id = user_id if user_id is not None else team.user_id
    if user_id is None:
        user_id = "default"

    return await team.memory_manager.aget_user_memories(user_id=user_id)  # type: ignore


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------


def _process_learnings(
    team: "Team",
    run_messages: RunMessages,
    session: TeamSession,
    user_id: Optional[str],
) -> Optional[RunMetrics]:
    """Process learnings from conversation (runs in background thread)."""
    if team._learning is None:
        return None

    from egpt.metrics import RunMetrics

    collector = RunMetrics()
    try:
        messages = list(run_messages.messages) if run_messages else []
        team._learning.process(
            messages=messages,
            user_id=user_id,
            session_id=session.session_id if session else None,
            team_id=team.id,
            run_metrics=collector,
        )
        log_debug("Learning extraction completed.")
    except Exception as e:
        log_warning(f"Error processing learnings: {str(e)}")
    return collector


async def _aprocess_learnings(
    team: "Team",
    run_messages: RunMessages,
    session: TeamSession,
    user_id: Optional[str],
) -> Optional[RunMetrics]:
    """Async process learnings from conversation."""
    if team._learning is None:
        return None

    from egpt.metrics import RunMetrics

    collector = RunMetrics()
    try:
        messages = list(run_messages.messages) if run_messages else []
        await team._learning.aprocess(
            messages=messages,
            user_id=user_id,
            session_id=session.session_id if session else None,
            team_id=team.id,
            run_metrics=collector,
        )
        log_debug("Learning extraction completed.")
    except Exception as e:
        log_warning(f"Error processing learnings: {str(e)}")
    return collector


def _start_learning_future(
    team: "Team",
    run_messages: RunMessages,
    session: TeamSession,
    user_id: Optional[str],
    existing_future: Optional[Future] = None,
) -> Optional[Future]:
    """Start learning extraction in background thread.

    Args:
        team: The Team instance.
        run_messages: The run messages containing conversation.
        session: The team session.
        user_id: The user ID for learning extraction.
        existing_future: An existing future to cancel before starting a new one.

    Returns:
        A new learning future if conditions are met, None otherwise.
    """
    if existing_future is not None and not existing_future.done():
        existing_future.cancel()

    if team._learning is not None:
        log_debug("Starting learning extraction in background thread.")
        return team.background_executor.submit(
            _process_learnings,
            team,
            run_messages=run_messages,
            session=session,
            user_id=user_id,
        )

    return None


async def _astart_learning_task(
    team: "Team",
    run_messages: RunMessages,
    session: TeamSession,
    user_id: Optional[str],
    existing_task: Optional[asyncio.Task[Optional[RunMetrics]]] = None,
) -> Optional[asyncio.Task[Optional[RunMetrics]]]:
    """Start learning extraction as async task.

    Args:
        team: The Team instance.
        run_messages: The run messages containing conversation.
        session: The team session.
        user_id: The user ID for learning extraction.
        existing_task: An existing task to cancel before starting a new one.

    Returns:
        A new learning task if conditions are met, None otherwise.
    """
    if existing_task is not None and not existing_task.done():
        existing_task.cancel()
        try:
            await existing_task
        except asyncio.CancelledError:
            pass

    if team._learning is not None:
        log_debug("Starting learning extraction as async task.")
        return asyncio.create_task(
            _aprocess_learnings(
                team,
                run_messages=run_messages,
                session=session,
                user_id=user_id,
            )
        )

    return None


# ---------------------------------------------------------------------------
# Memory provider (SM-X3): feed-forward read + write-back capture through the
# memory seam (egpt.memory.provider.MemoryProvider). Additive and independent
# of the Memory section above -- separate field (team.memory_provider),
# separate code path from team.memory_manager.
# ---------------------------------------------------------------------------


def _memory_scope(
    team: "Team",
    run_context: "RunContext",
    user_id: Optional[str],
    *,
    for_read: bool = False,
) -> Optional[Scope]:
    """Build the Scope for this call, or None if the hooks should no-op.

    Gated on both team.memory_provider and run_context.tenant_id -- a run with no tenant
    configured is skipped rather than falling back to a sentinel tenant (SM-X3).

    Read and write scopes differ for the reasons spelled out on the agent twin
    (``agent/_managers.py:_memory_scope``): scope narrowing is conjunctive across every set slot,
    so ``session_id`` on a read makes cross-session recall impossible, and ``team_id`` is
    ``generate_id_from_name(team.name)`` -- uuid4-derived per construction when the team has
    neither ``id`` nor ``name``. Both stay on the write as provenance.
    """
    if team.memory_provider is None or run_context.tenant_id is None:
        return None
    try:
        return Scope(
            tenant_id=run_context.tenant_id,
            team_id=None if for_read else team.id,
            user_id=user_id,
            session_id=None if for_read else run_context.session_id,
        )
    except (ValueError, TypeError) as e:
        # See the agent twin: blank/whitespace slots are caller input and must degrade to
        # no-memory rather than failing the run.
        log_warning(f"Skipping memory hooks, invalid scope: {str(e)}")
        return None


def _get_memory_context(
    team: "Team",
    run_context: "RunContext",
    run_messages: RunMessages,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Feed-forward read hook: assemble persistent-tier memory context, or None if gated off.

    Synchronous and inline, not fire-and-forget: the result must be injected into
    run_messages.messages before the model call, so there is nothing to hand a background
    future the way the write-back hook does.
    """
    scope = _memory_scope(team, run_context, user_id, for_read=True)
    if scope is None:
        return None

    query_text = run_messages.user_message.get_content_string() if run_messages.user_message is not None else ""
    query = MemoryQuery(text=query_text)
    working = WorkingMemory(scope=scope)
    log_debug("Reading memory context for feed-forward injection.")
    try:
        return team.memory_provider.assemble_context(scope, working, query)  # type: ignore[union-attr]
    except Exception as e:
        log_warning(f"Error reading memory context: {str(e)}")
        return None


async def _aget_memory_context(
    team: "Team",
    run_context: "RunContext",
    run_messages: RunMessages,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Async variant of _get_memory_context."""
    scope = _memory_scope(team, run_context, user_id, for_read=True)
    if scope is None:
        return None

    query_text = run_messages.user_message.get_content_string() if run_messages.user_message is not None else ""
    query = MemoryQuery(text=query_text)
    working = WorkingMemory(scope=scope)
    log_debug("Reading memory context for feed-forward injection.")
    try:
        return await team.memory_provider.aassemble_context(scope, working, query)  # type: ignore[union-attr]
    except Exception as e:
        log_warning(f"Error reading memory context: {str(e)}")
        return None


def _start_memory_writeback_future(
    team: "Team",
    run_context: "RunContext",
    run_response: "TeamRunOutput",
    user_id: Optional[str] = None,
    existing_future: Optional[Future] = None,
) -> Optional[Future]:
    """Cancel any existing write-back future and start a new one if conditions are met.

    Off the model-call path, not the request path: the future is joined by
    wait_for_open_threads/await_for_thread_tasks_stream before the run returns. Mirrors
    _start_memory_future's dispatch shape. The dispatch primitives themselves
    (events_from_run, start_writeback_future) already handle empty-events and error
    logging -- this function only gates and builds the Scope/events.
    """
    if existing_future is not None and not existing_future.done():
        existing_future.cancel()

    scope = _memory_scope(team, run_context, user_id)
    if scope is None:
        return None

    from egpt.memory.writeback import events_from_run, run_is_paused, start_writeback_future, turn_messages

    if run_is_paused(run_response.tools):
        # HITL pause: the result and the answer land on the continue-run leg, which dispatches
        # its own write-back. Writing here would persist the question without them.
        return None

    events = events_from_run(
        scope,
        messages=turn_messages(run_response.messages),
        tool_executions=run_response.tools or (),
    )
    if not events:
        return None

    log_debug("Starting memory write-back in background thread.")
    return start_writeback_future(team.memory_provider, scope, events, executor=team.background_executor)  # type: ignore[arg-type]


async def _astart_memory_writeback_task(
    team: "Team",
    run_context: "RunContext",
    run_response: "TeamRunOutput",
    user_id: Optional[str] = None,
    existing_task: Optional[asyncio.Task] = None,
) -> Optional[asyncio.Task]:
    """Async variant of _start_memory_writeback_future."""
    if existing_task is not None and not existing_task.done():
        existing_task.cancel()
        try:
            await existing_task
        except asyncio.CancelledError:
            pass

    scope = _memory_scope(team, run_context, user_id)
    if scope is None:
        return None

    from egpt.memory.writeback import astart_writeback_task, events_from_run, run_is_paused, turn_messages

    if run_is_paused(run_response.tools):
        # HITL pause: the result and the answer land on the continue-run leg, which dispatches
        # its own write-back. Writing here would persist the question without them.
        return None

    events = events_from_run(
        scope,
        messages=turn_messages(run_response.messages),
        tool_executions=run_response.tools or (),
    )
    if not events:
        return None

    log_debug("Starting memory write-back in background task.")
    return astart_writeback_task(team.memory_provider, scope, events)  # type: ignore[arg-type]
