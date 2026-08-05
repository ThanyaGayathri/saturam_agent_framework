"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.moonshot.moonshot`` instead; this module will be removed.
"""

from egpt.integrations.models.moonshot import moonshot as _target


def __getattr__(name: str):
    return getattr(_target, name)
