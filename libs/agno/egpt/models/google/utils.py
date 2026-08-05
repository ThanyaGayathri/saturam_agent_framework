"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.google.utils`` instead; this module will be removed.
"""

from egpt.integrations.models.google import utils as _target


def __getattr__(name: str):
    return getattr(_target, name)
