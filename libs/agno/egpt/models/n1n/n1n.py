"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.n1n.n1n`` instead; this module will be removed.
"""

from egpt.integrations.models.n1n import n1n as _target


def __getattr__(name: str):
    return getattr(_target, name)
