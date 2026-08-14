"""Sub-agent construction seam: build a runnable by name, without importing one.

EGPT-1.7a moves the context connectors into ``integrations/``, and several of them build a
sub-agent to wrap a toolkit. ``egpt.agent.Agent`` is orchestration, and ``integrations -> core
only`` is exactly as hard a rule as ``core -> orchestration only`` -- so a connector cannot
``from egpt.agent import Agent`` any more than a core module could. This mirrors
``egpt.tools.utils.construct_toolkit``: resolve the class by name and forward kwargs to its
constructor, importing it only when a caller actually asks for one.

Deliberately not folded into ``egpt.registry.discovery``'s five-kind seam: that table exists
for genuinely pluggable adapters (a third party can register another model provider under a
new name), and is entry-point-capable for exactly that reason. There is one orchestration
runtime class, not a family of interchangeable ones, so a second, smaller table here avoids
growing the shared ``KINDS`` tuple and its pinned entry-point groups for a caller that was
never meant to be pluggable.

Construction is sync-only by design, matching ``construct_toolkit``: building an ``Agent``
object has nothing to await, so an async variant would be dead weight.
"""

import importlib
from typing import TYPE_CHECKING, Any, Dict, Tuple

if TYPE_CHECKING:
    from egpt.registry.protocols import RunnableLike

# name -> (module, class_name). One row today; "team" can be added by whoever needs it.
_ROWS: Dict[str, Tuple[str, str]] = {
    "agent": ("egpt.agent", "Agent"),
}


def get_runnable_class(name: str) -> type:
    """Import and return the runnable class registered under ``name``.

    Raises:
        ValueError: ``name`` has no registration.
    """
    if name not in _ROWS:
        known = ", ".join(sorted(_ROWS)) or "<none>"
        raise ValueError(f"Unknown runnable '{name}'. Registered runnables: {known}.")
    module, attr = _ROWS[name]
    target = importlib.import_module(module)
    return getattr(target, attr)


def construct_runnable(name: str, /, **kwargs: Any) -> "RunnableLike":
    """Build the runnable registered under ``name``, forwarding ``kwargs`` to its constructor.

    ``name`` is positional-only: ``Agent`` itself takes a ``name`` constructor argument, and a
    keyword ``name`` would otherwise collide with the lookup key.
    """
    return get_runnable_class(name)(**kwargs)
