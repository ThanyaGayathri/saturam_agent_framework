"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.llama_cpp.llama_cpp`` instead; this module will be removed.
"""

from egpt.integrations.models.llama_cpp import llama_cpp as _target


def __getattr__(name: str):
    return getattr(_target, name)
