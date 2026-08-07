"""Deprecated compatibility shim (EGPT-1.7).

Import from ``egpt.integrations.tools.sql`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitations:

* ``mock.patch`` against this module patches the shim's binding, not the implementation in
  the target module. Patch the target path instead.
* Submodule identity is order-dependent. ``from egpt.tools.sql import sql`` resolves
  through the ``__getattr__`` below to the *target* submodule and registers no
  ``egpt.tools.sql.sql`` entry in ``sys.modules``, whereas
  ``import egpt.tools.sql.sql`` loads the shim submodule. The same dotted path can
  therefore name two different module objects depending on which route ran first. Calls are
  unaffected -- both routes reach the same function objects -- but it compounds the
  ``mock.patch`` limitation above: a patch aimed at the legacy dotted path may bind a module
  the implementation never reads.
"""

import warnings as _warnings

import egpt.integrations.tools.sql as _target
from egpt.integrations.tools.sql.sql import (  # noqa: F401
    SQLTools,
)

_warnings.warn(
    "egpt.tools.sql is deprecated; import from egpt.integrations.tools.sql instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "SQLTools",
]


def __dir__():
    # __all__ rather than globals() alone: names the target provides lazily are resolved
    # by __getattr__ below and never land in this module's globals, so the default module
    # dir() would omit them despite them being importable and listed in __all__.
    return sorted(set(globals()) | set(__all__))


def __getattr__(name: str):
    # Fallback for names the target provides lazily or via an optional-dependency
    # fallback, which must not be imported eagerly.
    return getattr(_target, name)
