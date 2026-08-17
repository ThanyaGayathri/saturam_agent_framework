"""Deprecated compatibility shim (EGPT-1.7b, aicoe-tracker#158).

Import from ``egpt.integrations.channels.slack.pause`` instead; this module will be removed.

Names are re-exported via a wildcard star-import against the target module (not proxied
through ``__getattr__`` alone), so star-imports, ``dir()`` and static type checking keep
working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.channels.slack.pause as _target
from egpt.integrations.channels.slack.pause import *  # noqa: F401,F403

_warnings.warn(
    "egpt.os.interfaces.slack.pause is deprecated; import from "
    "egpt.integrations.channels.slack.pause instead. This shim will be removed in a "
    "future release.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    return getattr(_target, name)
