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
first interactive click -- deliberately not ``isinstance(entity, HitlRunnableLike)``: this
protocol has only callable members, and Python 3.12's runtime-checkable ``isinstance`` for a
callable-only protocol resolves via ``issubclass(type(entity), ...)``, which inspects the
*class's* ``__dict__`` and ignores instance-level attributes -- it misclassifies genuine
duck-typed objects and every ``Mock``/``AsyncMock``-based test double as non-conforming
regardless of what the instance actually exposes. This protocol still exists for ``hitl.py``'s
own static typing (see ``HITLHandler.entity``), just not for the runtime setup check. WhatsApp's
router and Slack's own ``event_handler.py`` only ever call ``entity.arun(...)`` -- never the two
HITL-only members -- so they keep using plain ``AsyncRunnableLike``.

This module must keep importing nothing but ``typing`` -- that is the whole point of it.
"""

from typing import Any, Optional, Protocol, runtime_checkable


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
