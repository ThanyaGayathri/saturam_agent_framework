"""Deprecated compatibility shim (EGPT-1.5).

Import from ``egpt.integrations.vectordb.lancedb`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.vectordb.lancedb as _target
from egpt.integrations.vectordb.lancedb import (  # noqa: F401
    LanceDb,
    SearchType,
)

_warnings.warn(
    "egpt.vectordb.lancedb is deprecated; import from egpt.integrations.vectordb.lancedb instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "LanceDb",
    "SearchType",
]


def __getattr__(name: str):
    # Fallback so a name added to the target later still resolves through this path.
    # No vectordb target binds names lazily or behind an optional-dependency guard today,
    # unlike the EGPT-1.4 provider shims this mirrors.
    return getattr(_target, name)
