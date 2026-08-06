"""Deprecated compatibility shim (EGPT-1.7).

Import from ``egpt.integrations.tools.neo4j.neo4j`` instead; this module will be removed.

The star-import keeps ``from egpt.tools.neo4j.neo4j import *`` and plain attribute access
working on the legacy dotted path. ``mock.patch`` against this module patches the shim's
binding rather than the implementation -- patch the target path instead.
"""

import warnings as _warnings

from egpt.integrations.tools.neo4j.neo4j import *  # noqa: F401,F403

_warnings.warn(
    "egpt.tools.neo4j.neo4j is deprecated; import from "
    "egpt.integrations.tools.neo4j.neo4j instead. This shim will be removed in a future "
    "release.",
    DeprecationWarning,
    stacklevel=2,
)
