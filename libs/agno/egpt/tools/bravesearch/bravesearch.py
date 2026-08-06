"""Deprecated compatibility shim (EGPT-1.7).

Import from ``egpt.integrations.tools.bravesearch.bravesearch`` instead; this module will be removed.

The star-import keeps ``from egpt.tools.bravesearch.bravesearch import *`` and plain attribute access
working on the legacy dotted path. ``mock.patch`` against this module patches the shim's
binding rather than the implementation -- patch the target path instead.
"""

import warnings as _warnings

from egpt.integrations.tools.bravesearch.bravesearch import *  # noqa: F401,F403

_warnings.warn(
    "egpt.tools.bravesearch.bravesearch is deprecated; import from "
    "egpt.integrations.tools.bravesearch.bravesearch instead. This shim will be removed in a future "
    "release.",
    DeprecationWarning,
    stacklevel=2,
)
