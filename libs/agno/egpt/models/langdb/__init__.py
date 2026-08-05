"""Deprecated compatibility shim (EGPT-1.4).

Model providers moved to the integrations layer. Import from
``egpt.integrations.models.langdb`` instead; this module will be removed.

Attribute access is forwarded rather than re-exported so the target package's
lazy imports and optional-dependency fallbacks keep working unchanged.
"""

from egpt.integrations.models import langdb as _target


def __getattr__(name: str):
    return getattr(_target, name)
