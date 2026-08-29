"""Managed prompt resolution for EGPT agents (EGPT-4.4).

Public API:

- :class:`ManagedPrompt` — typed reference that an ``Agent.system_message``
  or ``Agent.instructions`` can be set to. Resolved at run-time by the
  message-building pipeline.
- :class:`PromptManager` — fetches a prompt from Langfuse (with local
  fallback and short TTL cache). Usually consumed indirectly through
  :func:`resolve_managed_prompt`.
- :func:`resolve_managed_prompt` / :func:`aresolve_managed_prompt` —
  convenience wrappers called from ``agent/_messages.py``.
"""

from egpt.prompts.managed import ManagedPrompt
from egpt.prompts.manager import PromptManager, ResolvedPrompt, aresolve_managed_prompt, resolve_managed_prompt

__all__ = [
    "ManagedPrompt",
    "PromptManager",
    "ResolvedPrompt",
    "aresolve_managed_prompt",
    "resolve_managed_prompt",
]
