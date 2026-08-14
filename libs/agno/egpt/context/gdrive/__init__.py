"""Deprecated compatibility shim (EGPT-1.7a).

Import from ``egpt.integrations.context.gdrive`` instead; this module will be removed.

Names are re-exported explicitly and listed in ``__all__``, with a ``__getattr__``
fallback for names the target provides lazily.

Known limitations:

* ``mock.patch`` against this module patches the shim's binding, not the implementation in
  the target module. Patch the target path instead.
* Submodule identity is order-dependent. ``from egpt.context.gdrive import <sub>`` resolves
  through the ``__getattr__`` below to the *target* submodule and registers no
  ``egpt.context.gdrive.<sub>`` entry in ``sys.modules``, whereas ``import egpt.context.gdrive.<sub>``
  loads the shim submodule. The same dotted path can therefore name two different module
  objects depending on which route ran first. Calls are unaffected -- both routes reach the
  same function objects -- but it compounds the ``mock.patch`` limitation above: a patch
  aimed at the legacy dotted path may bind a module the implementation never reads.
"""

import warnings as _warnings

import egpt.integrations.context.gdrive as _target
from egpt.integrations.context.gdrive import (
    DEFAULT_GDRIVE_INSTRUCTIONS,  # noqa: F401
    GDriveContextProvider,  # noqa: F401
    GoogleDriveContextProvider,  # noqa: F401
)

_warnings.warn(
    "egpt.context.gdrive is deprecated; import from egpt.integrations.context.gdrive instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["DEFAULT_GDRIVE_INSTRUCTIONS", "GoogleDriveContextProvider", "GDriveContextProvider"]


def __dir__():
    # __all__ rather than globals() alone: names the target provides lazily are resolved
    # by __getattr__ below and never land in this module's globals, so the default module
    # dir() would omit them despite them being importable and listed in __all__.
    return sorted(set(globals()) | set(__all__))


def __getattr__(name: str):
    # Fallback for names the target provides lazily or via an optional-dependency
    # fallback, which must not be imported eagerly.
    return getattr(_target, name)
