"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.dashscope.dashscope`` instead; this module will be removed.
"""

from egpt.integrations.models.dashscope import dashscope as _target


def __getattr__(name: str):
    return getattr(_target, name)
