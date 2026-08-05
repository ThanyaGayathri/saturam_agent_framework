"""Deprecated compatibility shim (EGPT-1.4).

Import from ``egpt.integrations.models.tuning_engines.tuning_engines`` instead; this module will be removed.
"""

from egpt.integrations.models.tuning_engines import tuning_engines as _target


def __getattr__(name: str):
    return getattr(_target, name)
