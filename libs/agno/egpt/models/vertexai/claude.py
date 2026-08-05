"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.vertexai.claude`` instead; this module will be removed.
"""

from egpt.integrations.models.vertexai import claude as _target


def __getattr__(name: str):
    return getattr(_target, name)
