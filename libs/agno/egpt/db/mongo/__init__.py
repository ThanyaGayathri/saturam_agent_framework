"""Deprecated compatibility shim (EGPT-1.6).

Import from ``egpt.integrations.db.mongo`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitations:

* ``mock.patch`` against this module patches the shim's binding, not the implementation in
  the target module. Patch the target path instead.
* Submodule identity is order-dependent. ``from egpt.db.mongo import <sub>`` resolves through
  the ``__getattr__`` below to the *target* submodule and registers no
  ``egpt.db.mongo.<sub>`` entry in ``sys.modules``, whereas ``import egpt.db.mongo.<sub>``
  loads the shim submodule. The same dotted path can therefore name two different module
  objects depending on which route ran first. Calls are unaffected -- both routes reach the
  same function objects -- but it compounds the ``mock.patch`` limitation above: a patch
  aimed at the legacy dotted path may bind a module the implementation never reads.

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


def __dir__():
    # __all__ rather than globals() alone: names the target provides lazily are resolved
    # by __getattr__ below and never land in this module's globals, so the default module
    # dir() would omit them despite them being importable and listed in __all__.
    return sorted(set(globals()) | set(__all__))


def __getattr__(name: str):
    # Fallback for names the target provides lazily or via an optional-dependency
    # fallback, which must not be imported eagerly.
    return getattr(_target, name)
