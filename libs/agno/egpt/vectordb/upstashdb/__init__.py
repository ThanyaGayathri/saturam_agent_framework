"""Deprecated compatibility shim (EGPT-1.5).

Import from ``egpt.integrations.vectordb.upstashdb`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.vectordb.upstashdb as _target
from egpt.integrations.vectordb.upstashdb import UpstashVectorDb  # noqa: F401

_warnings.warn(
    "egpt.vectordb.upstashdb is deprecated; import from egpt.integrations.vectordb.upstashdb instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "UpstashVectorDb",
]


def __getattr__(name: str):
    # Fallback so a name added to the target later still resolves through this path.
    # No vectordb target binds names lazily or behind an optional-dependency guard today,
    # unlike the EGPT-1.4 provider shims this mirrors.
    return getattr(_target, name)
