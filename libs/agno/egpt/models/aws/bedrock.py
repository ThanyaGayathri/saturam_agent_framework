"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.aws.bedrock`` instead; this module will be removed.
"""

from egpt.integrations.models.aws import bedrock as _target


def __getattr__(name: str):
    return getattr(_target, name)
