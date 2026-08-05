"""Deprecated compatibility shim (EGPT-1.6).

Import from ``egpt.integrations.db.redis`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.db.redis as _target
from egpt.integrations.db.redis.redis import RedisDb  # noqa: F401

_warnings.warn(
    "egpt.db.redis is deprecated; import from egpt.integrations.db.redis instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["RedisDb"]


def __getattr__(name: str):
    # Fallback for names the target provides lazily or via an optional-dependency
    # fallback, which must not be imported eagerly.
    return getattr(_target, name)
