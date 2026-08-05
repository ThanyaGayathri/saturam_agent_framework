"""Deprecated compatibility shim (EGPT-1.4).

``OpenAILike`` moved to ``egpt.integrations.models._openai_like``;
this module will be removed.
"""

from egpt.integrations.models._openai_like.like import OpenAILike

__all__ = ["OpenAILike"]
