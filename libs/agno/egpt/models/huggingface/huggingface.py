"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.huggingface.huggingface`` instead; this module will be removed.
"""

from egpt.integrations.models.huggingface import huggingface as _target


def __getattr__(name: str):
    return getattr(_target, name)
