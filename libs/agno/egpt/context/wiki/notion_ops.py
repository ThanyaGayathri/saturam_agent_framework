"""Deprecated compatibility shim (EGPT-1.7a).

Import from ``egpt.integrations.context.wiki.notion_ops`` instead; this module will be removed.

Names are re-exported via a wildcard star-import against the target module (not proxied
through ``__getattr__`` alone), so star-imports, ``dir()`` and static type checking keep
working on this path.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

from egpt.integrations.context.wiki.notion_ops import *  # noqa: F401,F403

_warnings.warn(
    "egpt.context.wiki.notion_ops is deprecated; import from egpt.integrations.context.wiki.notion_ops instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)
