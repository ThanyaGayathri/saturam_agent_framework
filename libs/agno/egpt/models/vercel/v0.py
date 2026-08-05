"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.vercel.v0`` instead; this module will be removed.
"""

from egpt.integrations.models.vercel import v0 as _target


def __getattr__(name: str):
    return getattr(_target, name)
