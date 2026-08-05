"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.neosantara.neosantara`` instead; this module will be removed.
"""

from egpt.integrations.models.neosantara import neosantara as _target


def __getattr__(name: str):
    return getattr(_target, name)
