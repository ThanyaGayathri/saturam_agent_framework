"""Deprecated compatibility shim (EGPT-1.6).

Import from ``egpt.integrations.db.mongo`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.

``AsyncMongoDb`` is deliberately NOT imported eagerly: the target package provides it
through its own ``__getattr__`` so that ``motor`` is not pulled in by a bare
``import egpt.db.mongo``. The ``__getattr__`` fallback below preserves that.
"""

import warnings as _warnings
from typing import TYPE_CHECKING

import egpt.integrations.db.mongo as _target
from egpt.integrations.db.mongo.mongo import MongoDb  # noqa: F401

if TYPE_CHECKING:
    from egpt.integrations.db.mongo.async_mongo import AsyncMongoDb  # noqa: F401

_warnings.warn(
    "egpt.db.mongo is deprecated; import from egpt.integrations.db.mongo instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


__all__ = ["AsyncMongoDb", "MongoDb"]


def __getattr__(name: str):
    # Fallback for names the target provides lazily or via an optional-dependency
    # fallback, which must not be imported eagerly.
    return getattr(_target, name)
