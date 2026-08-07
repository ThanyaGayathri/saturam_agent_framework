"""Deprecated compatibility shim (EGPT-1.7).

Import from ``egpt.integrations.tools.google`` instead; this module will be removed.

Every name is resolved lazily through ``__getattr__``, mirroring the target package:
eager re-exports here would force every optional Google SDK to import as a side effect
of touching this path.

Known limitations:

* ``mock.patch`` against this module patches the shim's binding, not the implementation in
  the target module. Patch the target path instead.
* Submodule identity is order-dependent. ``from egpt.tools.google import <sub>`` resolves through the
  ``__getattr__`` below to the *target* submodule and registers no ``egpt.tools.google.<sub>`` entry in
  ``sys.modules``, whereas ``import egpt.tools.google.<sub>`` loads the shim submodule. The same dotted
  path can therefore name two different module objects depending on which route ran first.
  Calls are unaffected -- both routes reach the same function objects -- but it compounds
  the ``mock.patch`` limitation above.
"""

import warnings as _warnings

import egpt.integrations.tools.google as _target

_warnings.warn(
    "egpt.tools.google is deprecated; import from egpt.integrations.tools.google instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "AuthConfig",
    "GoogleAuth",
    "GoogleSlidesTools",
    "GoogleBigQueryTools",
    "GoogleCalendarTools",
    "GoogleDriveTools",
    "GmailTools",
    "GoogleMapTools",
    "GoogleSheetsTools",
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
