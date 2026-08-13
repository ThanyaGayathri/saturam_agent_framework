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
