"""Deprecated compatibility shim (EGPT-1.7).

Import from ``egpt.integrations.tools.nano_banana.nano_banana`` instead; this module will be removed.

The star-import keeps ``from egpt.tools.nano_banana.nano_banana import *`` and plain attribute access
working on the legacy dotted path. ``mock.patch`` against this module patches the shim's
binding rather than the implementation -- patch the target path instead.
"""

import warnings as _warnings

from egpt.integrations.tools.nano_banana.nano_banana import *  # noqa: F401,F403

_warnings.warn(
    "egpt.tools.nano_banana.nano_banana is deprecated; import from "
    "egpt.integrations.tools.nano_banana.nano_banana instead. This shim will be removed in a future "
    "release.",
    DeprecationWarning,
    stacklevel=2,
)
