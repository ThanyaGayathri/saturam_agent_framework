"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.groq.groq`` instead; this module will be removed.
"""

from egpt.integrations.models.groq import groq as _target


def __getattr__(name: str):
    return getattr(_target, name)
