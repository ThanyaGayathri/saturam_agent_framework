"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.openrouter.responses`` instead; this module will be removed.
"""

from egpt.integrations.models.openrouter import responses as _target


def __getattr__(name: str):
    return getattr(_target, name)
