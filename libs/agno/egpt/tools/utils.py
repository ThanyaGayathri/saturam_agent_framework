"""Toolkit construction seam: resolve a concrete toolkit by name, without importing one.

ADR 0001 forbids core, services and orchestration from importing a concrete adapter, but
some of them still have to *build* one -- the Slack context provider constructs
``SlackTools`` with a per-agent capability set that no composition root can pre-instantiate.

EGPT-1.9 folded the toolkit rows into the shared discovery seam
(``egpt.registry.discovery``), alongside models, dbs, vector dbs and embedders, so this
module is now a thin forwarder that keeps the two names its callers already import. The
rows themselves live in ``egpt/registry/_declarations/tools.py``, which also carries the
reasoning for why that table stays deliberately sparse.

Construction is sync-only by design: ``construct_toolkit`` builds an object and has nothing
to await, so an async variant would be dead weight (same rationale as
``BaseDb.clone_with_table_overrides``).
"""

from typing import TYPE_CHECKING, Any

from egpt.registry import discovery

if TYPE_CHECKING:
    from egpt.tools.toolkit import Toolkit


def get_toolkit_class(name: str) -> type:
    """Import and return the toolkit class registered under ``name``.

    Raises:
        ValueError: ``name`` has no registration. The message says "toolkit", not the seam's
            generic "tool": these are ``Toolkit`` subclasses, and this codebase also calls a
            bare ``Function`` a tool, so the generic word would name the wrong thing.
        ImportError: the toolkit's optional dependency is not installed. The message is the
            toolkit's own, which names the package to install -- wrapping it here would
            replace actionable advice with a generic failure.
    """
    registration = discovery.get("tool", name)
    if registration is None:
        known = ", ".join(discovery.names("tool")) or "<none>"
        raise ValueError(f"Unknown toolkit '{name}'. Registered toolkits: {known}.")
    return registration.load()


def construct_toolkit(name: str, **kwargs: Any) -> "Toolkit":
    """Build the toolkit registered under ``name``, forwarding ``kwargs`` to its constructor."""
    return get_toolkit_class(name)(**kwargs)
