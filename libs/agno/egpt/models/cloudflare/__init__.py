"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.cloudflare`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.models.cloudflare as _target
from egpt.integrations.models.cloudflare.cloudflare import (
    DEFAULT_GATEWAY_MODEL,  # noqa: F401
    Cloudflare,  # noqa: F401
    normalize_cloudflare_gateway_model_id,  # noqa: F401
)

_warnings.warn(
    "egpt.models.cloudflare is deprecated; import from egpt.integrations.models.cloudflare instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "Cloudflare",
    "DEFAULT_GATEWAY_MODEL",
    "normalize_cloudflare_gateway_model_id",
]


def __getattr__(name: str):
    # Fallback for names the target provides lazily (google's GeminiInteractions) or via
    # an optional-dependency fallback (aws Claude), which must not be imported eagerly.
    return getattr(_target, name)
