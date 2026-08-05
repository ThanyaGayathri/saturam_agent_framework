"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.azure.ai_foundry`` instead; this module will be removed.
"""

from egpt.integrations.models.azure import ai_foundry as _target


def __getattr__(name: str):
    return getattr(_target, name)
