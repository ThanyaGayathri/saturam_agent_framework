"""Deprecated compatibility shim (EGPT-1.4).

Moved to ``egpt.integrations.models._utils.schema_utils`` with the model providers; this module will be removed.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.models._utils.schema_utils as _target
from egpt.integrations.models._utils.schema_utils import *  # noqa: F401,F403

_warnings.warn(
    "egpt.utils.models.schema_utils is deprecated; import from egpt.integrations.models._utils.schema_utils instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    return getattr(_target, name)
