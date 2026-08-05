"""Deprecated compatibility shim (EGPT-1.4).

``OpenAILike`` moved to ``egpt.integrations.models.openai_like``; this module will be removed.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.models.openai_like as _target
from egpt.integrations.models.openai_like import OpenAILike  # noqa: F401

_warnings.warn(
    "egpt.models.openai.like is deprecated; import from egpt.integrations.models.openai_like "
    "instead. This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["OpenAILike"]


def __getattr__(name: str):
    return getattr(_target, name)
