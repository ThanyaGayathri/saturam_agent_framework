"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.groq.groq`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.models.groq.groq as _target
from egpt.integrations.models.groq.groq import *  # noqa: F401,F403

_warnings.warn(
    "egpt.models.groq.groq is deprecated; import from egpt.integrations.models.groq.groq instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    # Fallback for names the target provides lazily (google's GeminiInteractions) or via
    # an optional-dependency fallback (aws Claude), which must not be imported eagerly.
    return getattr(_target, name)
