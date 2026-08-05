"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.nexus.nexus`` instead; this module will be removed.
"""

from egpt.integrations.models.nexus import nexus as _target


def __getattr__(name: str):
    return getattr(_target, name)
