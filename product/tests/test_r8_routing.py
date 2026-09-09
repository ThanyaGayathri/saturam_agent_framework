"""R8 -- model routing (G6), without a model or a database.

What is pinned here: the routing table covers every registered agent (an unrouted agent
refuses assembly, naming itself); the small tier declares a context window and the
composition is the framework's escalation provider, not a product copy of it; the pre-call
rule fires *before* the small tier is asked; and R7's provenance reads the tier off the
table by executor name rather than warning and writing "none".
"""

from __future__ import annotations

import logging

import pytest
from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.model_provider import ModelResolveRequest
from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.components import (
    JOIN_PATH_EXPLAINER_ID,
    MEMORY_WRITING_AGENT_IDS,
    SYNONYM_PROPOSER_ID,
    build_agents,
)
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.provenance import _model_tier
from egpt_retail.routing import (
    NO_MODEL_TIER,
    ROUTING_TABLE,
    Tier,
    UnroutedAgentError,
    assert_every_agent_routed,
    model_tier_for,
    tier_for,
)
from egpt_retail.routing.providers import (
    AZURE_PROVIDER_KEY,
    TierModelProvider,
    build_tier_providers,
    provider_for,
    route_agents,
)

_DEAD_DB = "postgresql+psycopg://nobody:nope@127.0.0.1:1/none"


def _settings(**overrides: object) -> RetailSettings:
    return RetailSettings(retail_db_url=_DEAD_DB, **overrides)  # type: ignore[arg-type]


def _agents(settings: RetailSettings | None = None) -> list:
    settings = settings or _settings()
    return build_agents(settings, build_control_plane_db(settings))


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_every_registered_agent_has_a_row() -> None:
    """The table may name agents that do not exist yet; the reverse is the startup refusal."""
    ids = {agent.id for agent in _agents()}
    assert ids <= set(ROUTING_TABLE), f"unrouted: {ids - set(ROUTING_TABLE)}"
    assert {SYNONYM_PROPOSER_ID, JOIN_PATH_EXPLAINER_ID} <= ids


def test_the_tier_vocabulary_is_the_contracts() -> None:
    """`ModelTier` in schemas/configure.yaml is `[small, frontier]`; `ProvenanceModelTier`
    adds `none`. The enum and NO_MODEL_TIER must be exactly those, or PlanStep.model_tier and
    ProvenanceRecord.model_tier stop validating."""
    assert {tier.value for tier in Tier} == {"small", "frontier"}
    assert NO_MODEL_TIER == "none"


def test_model_tier_for_is_total() -> None:
    assert model_tier_for("join_path_explainer") == "small"
    assert model_tier_for("synonym_proposer") == "frontier"
    assert model_tier_for("check_preflight") == "none"
    assert model_tier_for("anonymous_function") == "none"
    assert model_tier_for(None) == "none"


def test_tier_for_refuses_an_unrouted_agent_by_name() -> None:
    with pytest.raises(UnroutedAgentError, match="nobody"):
        tier_for("nobody")


def test_assert_every_agent_routed_reports_every_fault_at_once() -> None:
    with pytest.raises(UnroutedAgentError) as excinfo:
        assert_every_agent_routed(["join_path_explainer", "ghost_a", "ghost_b"])
    message = str(excinfo.value)
    assert "ghost_a" in message and "ghost_b" in message
    assert "join_path_explainer" not in message.split("routed agents")[0].split(":")[-1]


def test_the_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        ROUTING_TABLE["x"] = Tier.SMALL  # type: ignore[index]


# ---------------------------------------------------------------------------
# The providers
# ---------------------------------------------------------------------------


def test_the_small_tier_declares_its_window_and_the_frontier_does_not() -> None:
    providers = build_tier_providers(
        _settings(
            retail_azure_small_deployment="tiny",
            retail_small_context_window=4_096,
            retail_azure_deployment="huge",
        )
    )
    small, frontier = providers.small.capabilities(), providers.frontier.capabilities()
    assert small.context_window == 4_096
    assert frontier.context_window is None, (
        "the frontier states no window; nothing routes on it"
    )
    assert small.model_ids == ("tiny",) and frontier.model_ids == ("huge",)
    assert small.provider_keys == frontier.provider_keys == (AZURE_PROVIDER_KEY,), (
        "one key each, so escalated_from names the key this deployment actually built under"
    )
    assert small.notes["tier"] == "small" and frontier.notes["tier"] == "frontier"
    assert (
        providers.small.name == "small-tier"
        and providers.frontier.name == "frontier-tier"
    )


def test_the_resolved_model_is_pinned_to_its_tier_deployment_on_the_client(
    monkeypatch,
) -> None:
    """Azure routes by the client's `azure_deployment` and ignores the request's `model`; the
    framework model falls back to AZURE_OPENAI_DEPLOYMENT when the client has none. With that
    set, an `id`-only model on both tiers would have sent every call to one deployment."""
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "whatever-the-shell-had")
    providers = build_tier_providers(
        _settings(retail_azure_small_deployment="tiny", retail_azure_deployment="huge")
    )
    small = providers.small.resolve(ModelResolveRequest(agent_id="join_path_explainer"))
    frontier = providers.frontier.resolve(
        ModelResolveRequest(agent_id="synonym_proposer")
    )
    assert (small.id, small.azure_deployment) == ("tiny", "tiny")
    assert (frontier.id, frontier.azure_deployment) == ("huge", "huge")


def test_small_tier_agents_get_the_frameworks_escalation_not_a_copy() -> None:
    providers = build_tier_providers(_settings())
    composed = provider_for(Tier.SMALL, providers)
    assert isinstance(composed, EscalatingModelProvider)
    assert composed.primary is providers.small
    assert composed.escalation_target is providers.frontier
    assert provider_for(Tier.FRONTIER, providers) is providers.frontier


def test_each_small_tier_agent_gets_its_own_composition() -> None:
    """EscalatingModelProvider is a mutable dataclass; two agents must not share one."""
    providers = build_tier_providers(_settings())
    assert provider_for(Tier.SMALL, providers) is not provider_for(
        Tier.SMALL, providers
    )


def test_a_request_over_the_small_window_escalates_before_the_small_tier_is_asked() -> (
    None
):
    """The G6 verify line, offline: the decision is made on the declared window, and the
    small tier is never resolved -- counted, not inferred."""
    providers = build_tier_providers(_settings(retail_small_context_window=1_000))
    calls: dict[str, int] = {"small": 0, "frontier": 0}
    original = TierModelProvider.resolve_detailed

    def counting(self: TierModelProvider, request: ModelResolveRequest):  # type: ignore[no-untyped-def]
        calls[self.tier.value] += 1
        return original(self, request)

    TierModelProvider.resolve_detailed = counting  # type: ignore[method-assign]
    try:
        composed = provider_for(Tier.SMALL, providers)
        within = composed.resolve_detailed(
            ModelResolveRequest(
                agent_id="join_path_explainer", estimated_input_tokens=999
            )
        )
        over = composed.resolve_detailed(
            ModelResolveRequest(
                agent_id="join_path_explainer", estimated_input_tokens=1_001
            )
        )
    finally:
        TierModelProvider.resolve_detailed = original  # type: ignore[method-assign]

    assert within.escalated_from is None and within.model.id == providers.small.model_id
    assert over.escalated_from == AZURE_PROVIDER_KEY
    assert over.reason == "input_exceeds_context_window"
    assert over.model.id == providers.frontier.model_id
    assert calls == {"small": 1, "frontier": 1}, (
        "the over-window request never reached the small tier"
    )


def test_build_agents_pins_no_model_and_routes_each_agent() -> None:
    agents = {agent.id: agent for agent in _agents()}
    assert all(agent.model is None for agent in agents.values())
    assert isinstance(
        agents[JOIN_PATH_EXPLAINER_ID].model_provider, EscalatingModelProvider
    )
    assert isinstance(agents[SYNONYM_PROPOSER_ID].model_provider, TierModelProvider)
    assert agents[SYNONYM_PROPOSER_ID].model_provider.tier is Tier.FRONTIER


def test_route_agents_refuses_an_unrouted_agent() -> None:
    class Ghost:
        id = "ghost"
        model = None

    with pytest.raises(UnroutedAgentError, match="ghost"):
        route_agents([Ghost()], build_tier_providers(_settings()))


def test_route_agents_refuses_a_pinned_model() -> None:
    """A pinned model outranks the provider by contract, so a pinned agent would be listed as
    routed while every run bypassed the table."""

    class Pinned:
        id = "join_path_explainer"
        model = object()

    with pytest.raises(UnroutedAgentError, match="pin a model"):
        route_agents([Pinned()], build_tier_providers(_settings()))


def test_the_registered_agents_resolve_their_tier_deployment_at_startup() -> None:
    """`initialize_agent` -> `set_default_model` -> the provider. No credential is read."""
    settings = _settings(
        retail_azure_small_deployment="tiny", retail_azure_deployment="huge"
    )
    agents = {agent.id: agent for agent in _agents(settings)}
    for agent in agents.values():
        agent.initialize_agent()
    assert agents[JOIN_PATH_EXPLAINER_ID].model.id == "tiny"
    assert agents[SYNONYM_PROPOSER_ID].model.id == "huge"


def test_every_registered_agent_is_a_memory_writer() -> None:
    """The lifespan hands every agent the memory provider, so every agent writes a partition
    the consolidator must drain."""
    assert set(MEMORY_WRITING_AGENT_IDS) == {agent.id for agent in _agents()}


# ---------------------------------------------------------------------------
# Feeding R7
# ---------------------------------------------------------------------------


def _ran(executor_name: str | None) -> StepOutput:
    return StepOutput(content="x", metrics=object(), executor_name=executor_name)  # type: ignore[arg-type]


def test_provenance_reads_the_tier_off_the_table_by_executor_name(caplog) -> None:
    step_input = StepInput(
        previous_step_outputs={
            "preflight": StepOutput(content="ok", executor_name="check_preflight"),
            "explain": _ran("join_path_explainer"),
        }
    )
    with caplog.at_level(logging.WARNING):
        assert _model_tier(step_input, {}) == "small"
    assert not [r for r in caplog.records if "routing table" in r.message]


def test_provenance_reports_the_highest_tier_bound() -> None:
    step_input = StepInput(
        previous_step_outputs={
            "explain": _ran("join_path_explainer"),
            "propose": _ran("synonym_proposer"),
        }
    )
    assert _model_tier(step_input, {}) == "frontier"


def test_provenance_still_warns_for_a_model_step_outside_the_table(caplog) -> None:
    step_input = StepInput(
        previous_step_outputs={"mystery": _ran("some_unrouted_agent")}
    )
    with caplog.at_level(logging.WARNING):
        assert _model_tier(step_input, {}) == "none"
    assert any("some_unrouted_agent" in r.message for r in caplog.records)


def test_provenance_override_still_wins_over_the_table() -> None:
    step_input = StepInput(
        previous_step_outputs={"explain": _ran("join_path_explainer")}
    )
    assert _model_tier(step_input, {"model_tier": "frontier"}) == "frontier"
