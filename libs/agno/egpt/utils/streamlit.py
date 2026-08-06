"""Deprecated compatibility shim (EGPT-1.4).

Moved to ``egpt.integrations._utils.streamlit`` with the model providers; this module will be removed.

Known limitation: ``mock.patch`` against this module patches the shim's binding, not the
implementation in the target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations._utils.streamlit as _target
from egpt.integrations._utils.streamlit import *  # noqa: F401,F403

_warnings.warn(
    "egpt.utils.streamlit is deprecated; import from egpt.integrations._utils.streamlit instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    return getattr(_target, name)
