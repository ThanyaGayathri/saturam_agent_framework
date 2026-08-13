"""Deprecated compatibility shim (EGPT-1.7a).

Import from ``egpt.integrations.context.google.utils`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.context.google.utils as _target
from egpt.integrations.context.google.utils import *  # noqa: F401,F403

_warnings.warn(
    "egpt.context.google.utils is deprecated; import from egpt.integrations.context.google.utils instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    return getattr(_target, name)
