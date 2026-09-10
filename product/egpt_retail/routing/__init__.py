"""R8 -- model routing (G6): which tier serves which agent, composed through escalation.

Two tiers, ``small`` and ``frontier``, both Azure OpenAI deployments on the same Mode B code
path. Every registered agent is named in ``ROUTING_TABLE`` or the product refuses to start;
a small-tier agent is served through ``EscalatingModelProvider`` so a request the small
deployment provably cannot take -- more input than its declared window, a required
capability it lacks -- moves to the frontier *before* the call, and the move is written into
the run record by the framework's per-run hook. Deterministic executors never enter this
seam: a ``Step`` whose executor is a function resolves no model at all.

``table`` is pure and importable anywhere in the product (``executors/provenance.py`` reads
it); ``providers`` builds the provider objects and is only imported by ``app.components``.
"""

from egpt_retail.routing.table import (
    NO_MODEL_TIER,
    ROUTING_TABLE,
    Tier,
    UnroutedAgentError,
    assert_every_agent_routed,
    model_tier_for,
    tier_for,
)

__all__ = [
    "NO_MODEL_TIER",
    "ROUTING_TABLE",
    "Tier",
    "UnroutedAgentError",
    "assert_every_agent_routed",
    "model_tier_for",
    "tier_for",
]
