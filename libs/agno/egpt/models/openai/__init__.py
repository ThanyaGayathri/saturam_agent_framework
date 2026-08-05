"""Deprecated compatibility shim (EGPT-1.4).

Model providers moved to the integrations layer. Import from
``egpt.integrations.models.openai`` instead; this module will be removed.
``OpenAILike`` now lives in ``egpt.integrations.models._openai_like``.
"""

from egpt.integrations.models import openai as _target
from egpt.integrations.models._openai_like import OpenAILike

__all__ = [*_target.__all__, "OpenAILike"]


def __getattr__(name: str):
    return getattr(_target, name)
