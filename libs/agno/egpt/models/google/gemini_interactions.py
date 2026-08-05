"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.google.gemini_interactions`` instead; this module will be removed.
"""

from egpt.integrations.models.google import gemini_interactions as _target


def __getattr__(name: str):
    return getattr(_target, name)
