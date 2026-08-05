"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.requesty.requesty`` instead; this module will be removed.
"""

from egpt.integrations.models.requesty import requesty as _target


def __getattr__(name: str):
    return getattr(_target, name)
