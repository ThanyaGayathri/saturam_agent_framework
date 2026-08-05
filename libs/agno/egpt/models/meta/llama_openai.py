"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.meta.llama_openai`` instead; this module will be removed.
"""

from egpt.integrations.models.meta import llama_openai as _target


def __getattr__(name: str):
    return getattr(_target, name)
