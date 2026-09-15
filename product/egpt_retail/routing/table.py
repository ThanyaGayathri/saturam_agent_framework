"""The routing table: agent id to tier. Data, and the three pure lookups over it.

Keyed on the agent's ``id`` (which ``build_agents`` sets equal to its ``name``), because that
is the one identity that survives every seam this table feeds: ``ModelResolveRequest.agent_id``
on the provider side, ``StepOutput.executor_name`` on the workflow side (an agent step's
executor name *is* the agent's name), and ``PlanStep.agent_id`` in the API contract.

Rows for agents that do not exist yet are reserved names, not errors: R9's spec extractor and
R10's funnel explanation are routed here before they are built, so the package that builds
them cannot forget to decide. The reverse *is* an error -- see ``assert_every_agent_routed``.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Iterable, List, Mapping, Optional

from egpt_retail.contracts.use_case import TIER_FOR_ROLE

__all__ = [
    "NO_MODEL_TIER",
    "ROUTING_TABLE",
    "Tier",
    "UnroutedAgentError",
    "assert_every_agent_routed",
    "model_tier_for",
    "tier_for",
]


class Tier(str, Enum):
    """The two model tiers a step can be bound to.

    Exactly the API contract's ``ModelTier`` (``schemas/configure.yaml``), and the framework's
    ``MODEL_TIERS`` minus ``"none"`` -- ``none`` is not a tier an agent can be routed to, it is
    what a run reports when no agent ran (``NO_MODEL_TIER`` below).
    """

    SMALL = "small"
    FRONTIER = "frontier"


#: What ``model_tier_for`` answers for a name that is not an agent: a deterministic executor,
#: or nothing at all. Matches ``ProvenanceRecord.model_tier``'s vocabulary.
NO_MODEL_TIER = "none"

#: Which tier serves which agent. Read-only: a deployment does not retune routing at runtime,
#: and a table that could be edited in place would let a test's change leak into the next.
ROUTING_TABLE: Mapping[str, Tier] = MappingProxyType(
    {
        # Lane R's agents.
        "join_path_explainer": Tier.SMALL,  # K14's second half: mirrors a deterministic input
        "spec_extractor": Tier.SMALL,  # R9: "spec extraction on the small tier"
        "funnel_explanation": Tier.SMALL,  # R10: "explanation on the small tier"
        "playground": Tier.SMALL,  # R11: grounded chat, same posture as R10's explanation
        # Lane K's agents: proposals a reviewer approves, where the frontier's judgement is
        # the point and volume is a handful per refresh.
        "synonym_proposer": Tier.FRONTIER,
        "enrichment_proposer": Tier.FRONTIER,  # K8
        "mapping_proposer": Tier.FRONTIER,  # K8
        # K12 and K13: G6 puts suggestion on the frontier tier -- rare, quality-critical,
        # a handful of runs per refresh. Without these rows neither is a deployable agent;
        # the first cut ran them from cookbooks with a pinned model, bypassing routing.
        "kpi_suggestion": Tier.FRONTIER,  # K12
        "use_case_suggestion": Tier.FRONTIER,  # K13
    }
)


class UnroutedAgentError(LookupError):
    """A registered agent has no row in ``ROUTING_TABLE``.

    A ``LookupError`` because that is what it is, and raised at assembly rather than at the
    first request: K1's posture -- a deployment that cannot say which tier serves an agent
    must not come up and guess.
    """


def tier_for(agent_id: str) -> Tier:
    """The tier serving ``agent_id``, or ``UnroutedAgentError``."""
    try:
        return ROUTING_TABLE[agent_id]
    except KeyError:
        raise UnroutedAgentError(
            f"agent {agent_id!r} has no row in ROUTING_TABLE "
            f"(product/egpt_retail/routing/table.py); routed agents: {sorted(ROUTING_TABLE)}"
        ) from None


def model_tier_for(executor_name: Optional[str]) -> str:
    """The tier a workflow step's executor is bound to, in ``MODEL_TIERS`` vocabulary.

    Total: a name that is not a routed agent -- a deterministic executor such as
    ``check_preflight``, an anonymous function, ``None`` -- answers ``"none"``. That is what
    lets ``record_provenance`` fold every upstream step through one function without first
    asking which kind of step it was. The caller that *knows* a model ran (``metrics`` set)
    and still gets ``"none"`` has found an agent outside the table, and says so.
    """
    if executor_name is None:
        return NO_MODEL_TIER
    tier = ROUTING_TABLE.get(executor_name)
    if tier is not None:
        return tier.value
    # A compiled model step is named ``<use_case_id>__<role>`` (K15, ``compiler.py``) and
    # its tier follows from the role under G6 -- ``TIER_FOR_ROLE`` is the table for those,
    # so a use case activated yesterday is routed the same way as one shipped with the
    # product, without a row per use case here.
    _, separator, role = executor_name.rpartition("__")
    if separator and role in TIER_FOR_ROLE:
        return str(TIER_FOR_ROLE[role])
    return NO_MODEL_TIER


def assert_every_agent_routed(agent_ids: Iterable[str]) -> None:
    """Refuse, naming every unrouted agent at once, unless all of ``agent_ids`` have a row.

    Every fault in one message, the way ``assert_memory_wired`` reports memory: an operator
    adding two agents should not fix one, restart, and meet the other.
    """
    missing: List[str] = sorted(
        agent_id for agent_id in agent_ids if agent_id not in ROUTING_TABLE
    )
    if missing:
        raise UnroutedAgentError(
            f"{len(missing)} registered agent(s) have no row in ROUTING_TABLE "
            f"(product/egpt_retail/routing/table.py): {missing}. Every agent is routed to a "
            "tier or the product does not start."
        )
