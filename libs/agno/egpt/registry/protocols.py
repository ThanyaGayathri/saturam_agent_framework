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
