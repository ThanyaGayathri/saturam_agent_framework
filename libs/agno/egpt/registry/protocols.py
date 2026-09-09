"""Structural types for the orchestration objects the registry catalogs.

ADR 0001's allowed-import matrix says core imports nothing from orchestration, but
``Registry`` catalogs code-defined agents and teams so workflows can rehydrate them. Until
EGPT-1.9 it squared that with a ``TYPE_CHECKING`` import of ``Agent`` and ``Team`` -- which
costs nothing at runtime but still names orchestration in core, so the matrix could not be
mechanically enforced (docs/adr/0001-layered-architecture.md, known debt at "Runtime
construction seam", and extraction prerequisite 4).

These protocols carry the surface the registry's own callers actually use, so core depends
on a shape rather than on ``egpt.agent``. ``deep_copy`` is part of it because
``egpt/workflow/step.py`` calls it on whatever ``get_agent``/``get_team`` returns, to
isolate mutable state between concurrent requests; an ``id``-only protocol would fail
type-checking there.

``RunnableLike`` is the same idea for a different caller (EGPT-1.7a): a context connector
moved into ``integrations/`` still has to run a sub-agent, but ``integrations -> orchestration``
is exactly as forbidden as ``core -> orchestration`` is. It depends on this shape instead of
importing ``egpt.agent.Agent``, and gets a real instance from
``egpt.run.utils.construct_runnable``. The signature is intentionally loose --
``Agent.arun``/``Team.arun`` carry large ``@overload`` sets for streaming and typed output that
no caller here needs; every context provider just does ``await runnable.arun(query)``, or
(``ContextProvider._stream_from_agent``) ``async for event in runnable.arun(..., stream=True)``.
``arun`` is declared ``def``, not ``async def``, so both call shapes type-check against the
single ``Any`` return -- an ``async def`` here would make the real method's ``@overload``
between "returns a coroutine" and "returns an async iterator" collapse to just the former.

``AsyncRunnableLike`` is a third, narrower variant (EGPT-1.7b): the channel adapters moved into
``egpt.integrations.channels.*`` never construct an entity themselves (their composition-root
caller in ``egpt.os`` always passes one in already built), so ``construct_runnable`` does not
apply -- they only need to type the entity they were handed. ``RunnableLike`` cannot be reused
for this: ``RemoteAgent``/``RemoteTeam``/``RemoteWorkflow`` (``egpt.remote.base.BaseRemote``
subclasses) implement only ``arun``, not a sync ``run``, so they do not structurally satisfy
``RunnableLike``. Every channel-adapter call site only ever does ``await entity.arun(...)`` or
``async for event in entity.arun(..., stream=True)`` -- never ``.run()`` -- so the protocol drops
``run`` (and ``deep_copy``, which these callers never need either). It also drops ``id``, unlike
``AgentLike``/``TeamLike``/``RunnableLike`` above: no channel-adapter call site reads
``entity.id`` as an attribute (they only ever do ``getattr(entity, "id", None)``, which needs no
protocol membership), so a member with no consumer would just be unused surface -- add it back,
with a read-only ``@property`` (not a plain attribute: ``RemoteAgent``/``RemoteTeam``/
``RemoteWorkflow``'s ``id`` is read-only, and mypy rejects that against a plain, implicitly
settable protocol attribute), if and when a caller actually needs it.

``HitlRunnableLike`` is a fourth variant (EGPT-1.7b, PR #82 round-2 review): Slack's
``HITLHandler`` needs strictly more than ``AsyncRunnableLike`` -- resuming a paused run
(``acontinue_run``) and refetching a run's persisted output (``aget_run_output``), to read back
``active_requirements`` after an approval/rejection click. ``RemoteAgent``/``RemoteTeam``/
``RemoteWorkflow`` implement ``acontinue_run`` (via ``BaseRemote``) but none of them implement
``aget_run_output``, so they satisfy ``AsyncRunnableLike`` but not this narrower protocol.
Slack's ``attach_routes`` rejects a non-conforming entity at setup time with a ``hasattr``/
``callable`` capability check, rather than letting HITL fail with an ``AttributeError`` on the
first interactive click -- deliberately not ``isinstance(entity, HitlRunnableLike)``: for a
callable-only ``runtime_checkable`` protocol, ``isinstance`` resolves per-attribute via
``inspect.getattr_static`` (CPython's ``_ProtocolMeta.__instancecheck__``), which bypasses
``Mock``/``AsyncMock``'s dynamic ``__getattr__`` -- so mock-based test doubles fail the check even
though genuine duck-typed objects with real instance attributes pass fine. (It also never raises;
a non-conforming instance just makes the check return ``False``.) This protocol still exists for
``hitl.py``'s own static typing (see ``HITLHandler.entity``), just not for the runtime setup
check. WhatsApp's
router and Slack's own ``event_handler.py`` only ever call ``entity.arun(...)`` -- never the two
HITL-only members -- so they keep using plain ``AsyncRunnableLike``.

``ToolHandleLike`` is the toolkit-side variant (EGPT-1.7c, aicoe-tracker#180): tool functions
receive the live agent or team as a framework-injected parameter (``egpt/tools/function.py``
injects and schema-excludes it by parameter *name*, never by annotation, so the annotation is
free to be structural). Across every toolkit in ``egpt.integrations.tools`` the injected handle
is either passed through opaquely or used for exactly one thing: reading and lazily
initializing ``session_state``. That single member is the whole protocol; toolkits that never
touch the handle still annotate with it so the parameter documents what the framework injects
without naming ``egpt.agent``.

``ChatRunnableLike`` is the chat-client variant (EGPT-1.7c): ``egpt.integrations.discord``
drives a conversation loop -- ``arun`` a turn (per-message context rides in ``arun``'s own
kwargs), then ``acontinue_run`` when a paused run's tools are confirmed. It extends
``AsyncRunnableLike`` with ``acontinue_run`` (same member ``HitlRunnableLike`` declares, but
without ``aget_run_output``, which Discord never calls -- and ``HitlRunnableLike`` cannot be
reused whole for exactly that reason).

``SessionAgentLike`` is the session-UI variant (EGPT-1.7c): ``egpt.integrations._utils.streamlit``
renders session pickers and chat history, so it needs the session surface of an agent --
``session_id``, ``db``, ``get_session_name``/``set_session_name``, a sync streaming ``run``,
and the ``knowledge`` attribute its widgets read. Members the concrete classes only *may*
carry stay out of the contract: the tool-call display probes ``run_response`` with
``hasattr`` (no egpt entity is guaranteed to define it), and the private ``_agent_session``
reset uses ``hasattr``/``setattr`` (a leading-underscore member would write privates into a
public contract).

``SQLGuardLike`` is R6's variant (G3): ``egpt.integrations.tools.sql.sql`` (``SQLTools``) and
``egpt.integrations.context.database.provider`` (``DatabaseContextProvider``) both take an
optional SQL guard over their read path, but ``egpt.guardrails`` is exactly as forbidden to
``egpt.integrations`` as ``egpt.agent`` is (see ``libs/agno/pyproject.toml``'s "integrations
must not import orchestration, services, or api" contract, which lists ``egpt.guardrails``
alongside ``egpt.agent``/``egpt.team``/``egpt.workflow``). So the two integration modules type
their `guard`/`read_guard` parameter against this shape instead of importing
``egpt.guardrails.sql_guard.SQLGuard`` directly; the concrete class satisfies it structurally
with no changes on its side. Only the four members those two call sites actually use --
``check``/``acheck``/``effective_limit``/``statement_timeout_ms`` -- not the guard's full
public surface (``row_cap`` and ``dialect`` are constructor-only concerns neither caller
reads back).

This module must keep importing nothing but ``typing`` -- that is the whole point of it.
"""

from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class AgentLike(Protocol):
    """The part of an agent the registry and its callers depend on."""

    id: Optional[str]

    def deep_copy(self) -> Any:
        """Return an isolated copy, so concurrent runs do not share mutable state."""
        ...


@runtime_checkable
class TeamLike(Protocol):
    """The part of a team the registry and its callers depend on."""

    id: Optional[str]

    def deep_copy(self) -> Any:
        """Return an isolated copy, so concurrent runs do not share mutable state."""
        ...


@runtime_checkable
class RunnableLike(Protocol):
    """The part of a sub-agent an integrations-layer caller depends on: run it, get output."""

    id: Optional[str]

    def run(self, input: Any, **kwargs: Any) -> Any:
        """Sync counterpart of ``arun``, for callers on a sync ``query``/``update`` path."""
        ...

    def arun(self, input: Any, **kwargs: Any) -> Any:
        """Run with ``input``.

        Callers ``await`` this for a single run output, or ``async for`` it when
        ``stream=True`` -- see the module docstring for why the return type is ``Any``
        rather than ``Coroutine``.
        """
        ...


@runtime_checkable
class AsyncRunnableLike(Protocol):
    """The part of an entity a channel adapter depends on: run it async, get output."""

    def arun(self, *args: Any, **kwargs: Any) -> Any:
        """Run with the caller's ``input`` (and whatever else it passes); callers always
        ``await`` or ``async for`` this -- see the module docstring for why ``RunnableLike``
        (which also requires a sync ``run``) cannot be reused here. ``*args``/``**kwargs``
        rather than a named ``input`` parameter: ``Workflow.arun``'s overloads default
        ``input`` to ``None`` (``Agent.arun``/``Team.arun`` require it), and mypy's protocol
        conformance check for overloaded methods does not treat that as compatible with a
        single required-``input`` signature. A fully permissive signature sidesteps matching
        each concrete class's overload set individually.
        """
        ...


@runtime_checkable
class HitlRunnableLike(AsyncRunnableLike, Protocol):
    """The part of an entity Slack's HITL handler depends on: resume a paused run, refetch its
    output. A strict superset of ``AsyncRunnableLike`` -- see the module docstring for why
    ``RemoteAgent``/``RemoteTeam``/``RemoteWorkflow`` satisfy ``AsyncRunnableLike`` but not this.
    """

    def acontinue_run(self, *args: Any, **kwargs: Any) -> Any:
        """Resume a paused run with resolved approvals; ``*args``/``**kwargs`` for the same
        reason as ``AsyncRunnableLike.arun`` -- concrete signatures vary by entity type.
        """
        ...

    async def aget_run_output(self, *args: Any, **kwargs: Any) -> Any:
        """Refetch a run's persisted output. Declared ``async def``, unlike ``arun``/
        ``acontinue_run``: every concrete implementation is a plain coroutine, never a streaming
        iterator, so callers always ``await`` it -- no dual call-shape to preserve.
        """
        ...


@runtime_checkable
class ToolHandleLike(Protocol):
    """The part of a framework-injected agent/team handle a toolkit depends on.

    The only member any toolkit actually consumes is ``session_state``
    (``egpt.integrations.tools.{daytona,antigravity}`` read it and lazily assign ``{}``);
    every other toolkit either ignores the handle or forwards it opaquely to a user
    callback. Injection is by parameter name (see the module docstring), so this
    annotation is documentation plus static typing, never dispatch.
    """

    session_state: Optional[Dict[str, Any]]


@runtime_checkable
class ChatRunnableLike(AsyncRunnableLike, Protocol):
    """The part of an entity a chat client depends on: run a turn, resume a paused run.
    See the module docstring for why ``HitlRunnableLike`` (which also requires
    ``aget_run_output``) is not reused here.

    Deliberately behavioral -- no mutable attributes. Per-message context goes through
    ``arun``'s per-call kwargs (``dependencies``/``add_dependencies_to_context``); declaring
    a settable member here would ratify the set-then-run pattern, which races when the chat
    framework dispatches each inbound message as its own task against a shared entity.
    """

    def acontinue_run(self, *args: Any, **kwargs: Any) -> Any:
        """Resume a paused run with resolved approvals; ``*args``/``**kwargs`` for the same
        reason as ``AsyncRunnableLike.arun`` -- concrete signatures vary by entity type.
        """
        ...


@runtime_checkable
class SessionAgentLike(Protocol):
    """The session surface of an agent that a session-history UI depends on."""

    session_id: Optional[str]
    db: Any
    knowledge: Any

    def get_session_name(self) -> Any:
        """The display name of the current session (``None``/empty when unnamed)."""
        ...

    def set_session_name(self, *args: Any, **kwargs: Any) -> Any:
        """Rename the current session."""
        ...

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Sync run; callers iterate the return when ``stream=True`` -- ``*args``/``**kwargs``
        rather than a named ``input`` for the same overload-matching reason as
        ``AsyncRunnableLike.arun``.
        """
        ...


@runtime_checkable
class SQLGuardLike(Protocol):
    """The surface ``SQLTools``/``DatabaseContextProvider`` need from R6's SQL guard."""

    #: Read by ``SQLTools.run_sql`` to set Postgres's per-transaction ``SET LOCAL
    #: statement_timeout`` -- the one guard member a caller reads directly rather than
    #: through a method, because the timeout is applied to the session, not to the guard's
    #: own check.
    statement_timeout_ms: int

    def check(self, sql: str) -> Tuple[str, ...]:
        """Classify and validate ``sql``. Raises on refusal; returns the tables it reads."""
        ...

    async def acheck(self, sql: str) -> Tuple[str, ...]:
        """Async twin of :meth:`check`."""
        ...

    def effective_limit(self, requested: Optional[int], *, sql: str = "") -> int:
        """The fetch limit to actually use -- always ``<= row_cap``, regardless of ``requested``.
        ``sql`` rides along on the audit-log entry when a clamp occurs, so it can be
        correlated back to the query that triggered it."""
        ...
