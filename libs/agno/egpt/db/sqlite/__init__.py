"""Deprecated compatibility shim (EGPT-1.6).

Import from ``egpt.integrations.db.sqlite`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.db.sqlite as _target
from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb  # noqa: F401
from egpt.integrations.db.sqlite.sqlite import SqliteDb  # noqa: F401

_warnings.warn(
    "egpt.db.sqlite is deprecated; import from egpt.integrations.db.sqlite instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["AsyncSqliteDb", "SqliteDb"]


def __getattr__(name: str):
    # Fallback for names the target provides lazily or via an optional-dependency
    # fallback, which must not be imported eagerly.
    return getattr(_target, name)
