"""Deprecated compatibility shim (EGPT-1.7).

Import from ``egpt.integrations.tools.google.auth`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitations:

* ``mock.patch`` against this module patches the shim's binding, not the implementation in
  the target module. Patch the target path instead.
* Submodule identity is order-dependent. ``from egpt.tools.google.auth import <sub>`` resolves through the
  ``__getattr__`` below to the *target* submodule and registers no ``egpt.tools.google.auth.<sub>`` entry in
  ``sys.modules``, whereas ``import egpt.tools.google.auth.<sub>`` loads the shim submodule. The same dotted
  path can therefore name two different module objects depending on which route ran first.
  Calls are unaffected -- both routes reach the same function objects -- but it compounds
  the ``mock.patch`` limitation above.
"""

import warnings as _warnings

import egpt.integrations.tools.google.auth as _target
from egpt.integrations.tools.google.auth.credentials import (  # noqa: F401
    AuthConfig,
)
from egpt.integrations.tools.google.auth.decorator import (  # noqa: F401
    google_authenticate,
)
from egpt.integrations.tools.google.auth.toolkit import (  # noqa: F401
    GoogleAuth,
)

_warnings.warn(
    "egpt.tools.google.auth is deprecated; import from egpt.integrations.tools.google.auth instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "AuthConfig",
    "GoogleAuth",
    "google_authenticate",
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
