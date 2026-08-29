"""ManagedPrompt — typed wrapper for agent opt-in to managed prompts (EGPT-4.4).

Usage::

    from egpt.agent import Agent
    from egpt.prompts import ManagedPrompt

    agent = Agent(
        system_message=ManagedPrompt(
            name="customer-support-v1",
            label="production",
            variables={"company": "Acme"},
            fallback="You are a helpful customer-support agent for Acme.",
        ),
    )

The message-building pipeline in ``agent/_messages.py`` detects a
``ManagedPrompt`` instance and delegates to :func:`resolve_managed_prompt`
before continuing with normal string handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class ManagedPrompt:
    """An opt-in reference to a Langfuse-managed prompt.

    Parameters
    ----------
    name:
        Prompt name as registered in Langfuse.
    label:
        Optional Langfuse label (e.g. ``"production"``, ``"staging"``).
        When *None* the Langfuse SDK returns the prompt with the default
        label configured in Langfuse (typically ``"production"`` unless
        changed).  This is *not* necessarily the newest version — it is
        whichever version is promoted to that label.
    variables:
        Template variables to substitute into the prompt text.
        Note: ``dict`` is mutable even on a frozen dataclass — callers
        should not mutate after construction.
    fallback:
        Local fallback text used when Langfuse is unreachable or not
        configured.  Supports ``$variable`` / ``${variable}`` placeholders
        (``string.Template`` syntax).  When *None* and Langfuse is
        unavailable the resolver raises.
    """

    name: str
    label: Optional[str] = None
    variables: Dict[str, str] = field(default_factory=dict)
    fallback: Optional[str] = None
