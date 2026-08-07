"""Toolkit construction seam: resolve a concrete toolkit by name, without importing one.

ADR 0001 forbids core, services and orchestration from importing a concrete adapter, but
some of them still have to *build* one -- the Slack context provider constructs
``SlackTools`` with a per-agent capability set that no composition root can pre-instantiate.
This module is the tools counterpart of ``_DB_BACKENDS`` in ``egpt/db/utils.py`` and
``_PROVIDERS`` in ``egpt/models/utils.py``: one row per stable toolkit name, resolved
through ``importlib``, so the import edge is data rather than a static dependency.

The table is deliberately *not* a mirror of the ~130 toolkits in the integrations layer. A
row exists only for a toolkit an inward layer must construct by name; everything else is
imported directly by the caller that wants it (cookbooks, the control plane, user code) and
needs no seam. EGPT-1.9 folds these rows into ``Registry`` as ``construct_tool``, at which
point this module becomes a thin forwarder.

Construction is sync-only by design: ``construct_toolkit`` builds an object and has nothing
to await, so an async variant would be dead weight (same rationale as
``BaseDb.clone_with_table_overrides``).
"""

import importlib
from typing import TYPE_CHECKING, Any, Dict, Tuple

if TYPE_CHECKING:
    from egpt.tools.toolkit import Toolkit

# Stable toolkit name -> (module, class_name) in the integrations layer.
TOOLKIT_CLASSES: Dict[str, Tuple[str, str]] = {
    "slack": ("egpt.integrations.tools.slack", "SlackTools"),
}


def get_toolkit_class(name: str) -> type:
    """Import and return the toolkit class registered under ``name``.

    Raises:
        ValueError: ``name`` has no row in ``TOOLKIT_CLASSES``.
        ImportError: the toolkit's optional dependency is not installed. The message is the
            toolkit's own, which names the package to install -- wrapping it here would
            replace actionable advice with a generic failure.
    """
    entry = TOOLKIT_CLASSES.get(name)
    if entry is None:
        known = ", ".join(sorted(TOOLKIT_CLASSES)) or "<none>"
        raise ValueError(f"Unknown toolkit '{name}'. Registered toolkits: {known}.")

    module_name, class_name = entry
    return getattr(importlib.import_module(module_name), class_name)


def construct_toolkit(name: str, **kwargs: Any) -> "Toolkit":
    """Build the toolkit registered under ``name``, forwarding ``kwargs`` to its constructor."""
    return get_toolkit_class(name)(**kwargs)
