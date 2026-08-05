"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.azure`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.

The three names are imported from the target *package* rather than its submodules, so the
target's ``try``/``except ImportError`` stub fallbacks are honoured and no optional SDK is
forced open. Submodules stay reachable by their dotted path
(``egpt.models.azure.claude``); they are deliberately kept out of ``__all__``, matching
every provider that declared ``__all__`` before the move.
"""

import warnings as _warnings

import egpt.integrations.models.azure as _target
from egpt.integrations.models.azure import (  # noqa: F401
    AzureAIFoundry,
    AzureFoundryClaude,
    AzureOpenAI,
)

_warnings.warn(
    "egpt.models.azure is deprecated; import from egpt.integrations.models.azure instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "AzureAIFoundry",
    "AzureFoundryClaude",
    "AzureOpenAI",
]


def __getattr__(name: str):
    # Fallback for names the target provides lazily (google's GeminiInteractions) or via
    # an optional-dependency fallback (aws Claude), which must not be imported eagerly.
    return getattr(_target, name)
