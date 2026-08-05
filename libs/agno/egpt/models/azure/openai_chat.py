"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.azure.openai_chat`` instead; this module will be removed.
"""

from egpt.integrations.models.azure import openai_chat as _target


def __getattr__(name: str):
    return getattr(_target, name)
