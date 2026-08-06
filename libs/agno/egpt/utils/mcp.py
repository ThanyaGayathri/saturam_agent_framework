"""Deprecated compatibility shim (EGPT-1.7).

Moved to ``egpt.integrations._utils.mcp`` with the MCP client; this module will be removed.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations._utils.mcp as _target
from egpt.integrations._utils.mcp import *  # noqa: F401,F403

_warnings.warn(
    "egpt.utils.mcp is deprecated; import from egpt.integrations._utils.mcp instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    return getattr(_target, name)
