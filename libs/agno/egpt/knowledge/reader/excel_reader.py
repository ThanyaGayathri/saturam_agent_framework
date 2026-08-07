"""Deprecated compatibility shim (EGPT-1.8).

Import from
``egpt.integrations.knowledge.reader.excel_reader``
instead; this module will be removed.

The star-import keeps star-imports and plain attribute access working on the legacy dotted
path, and deliberately declares no ``__all__`` of its own so that surface stays exactly what
it was before the move -- an ``__all__`` here would narrow it. ``mock.patch`` against this
module patches the shim's binding rather than the implementation; patch the target instead.
"""

import warnings as _warnings

from egpt.integrations.knowledge.reader.excel_reader import *  # noqa: F401,F403

_warnings.warn(
    "egpt.knowledge.reader.excel_reader is deprecated; import from "
    "egpt.integrations.knowledge.reader.excel_reader instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)
