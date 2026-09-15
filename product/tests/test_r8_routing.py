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
from egpt.agent import AgentFactory
from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.model_provider import ModelResolveRequest
from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.components import (
    JOIN_PATH_EXPLAINER_ID,
    MEMORY_WRITING_AGENT_IDS,
    PLAYGROUND_AGENT_ID,
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


def _static_agents(settings: RetailSettings | None = None) -> list:
    """``build_agents`` also returns R11's ``AgentFactory`` (``PLAYGROUND_AGENT_ID``), which
    has no ``model``/``initialize_agent`` of its own -- routing tests that are specifically
    about a static ``Agent``'s pinned-model/tier-resolution wiring exclude it here rather
    than special-casing every assertion below."""
    return [agent for agent in _agents(settings) if not isinstance(agent, AgentFactory)]


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


def test_build_tier_providers_honors_a_non_default_model_provider() -> None:
    """#268: the Mode B provider is a setting, not hardcoded to Azure. Pure construction and
    capabilities() only -- resolving a non-Azure model needs that vendor's SDK installed, which
    the `unit-drivers-absent` CI job does not guarantee, so this does not call `.resolve()`."""
    providers = build_tier_providers(
        _settings(
            retail_model_provider="anthropic",
            retail_azure_small_deployment="claude-haiku-4-5",
            retail_azure_deployment="claude-sonnet-4-5",
        )
    )
    small, frontier = providers.small.capabilities(), providers.frontier.capabilities()
    assert small.provider_keys == frontier.provider_keys == ("anthropic",)
    assert small.model_ids == ("claude-haiku-4-5",)
    assert frontier.model_ids == ("claude-sonnet-4-5",)
    # Only Azure OpenAI needs its deployment pinned on the client (see TierModelProvider's
    # docstring); `capabilities()` never surfaces `_model_config` into `.notes` either way, so
    # this reads the provider's actual config directly rather than asserting on a key that is
    # never populated regardless of whether the pin fired.
    assert (
        providers.small._model_config == {} and providers.frontier._model_config == {}
    )


def test_a_whitespace_padded_model_provider_still_pins_the_azure_deployment() -> None:
    """A regression guard for the collision #268 was filed over: `FrontierModelProvider.__init__`
    `.strip()`s `provider_key` before storing it, and `RetailSettings` has no
    `str_strip_whitespace` config to normalize `EGPT_RETAIL_MODEL_PROVIDER` first (a stray space
    is a realistic `.env`/compose-interpolation artifact). Comparing the *raw* argument against
    `AZURE_PROVIDER_KEY` would fail this check while `capabilities()` still reported a clean
    'azure-openai' key -- both tiers silently collapsing onto AZURE_OPENAI_DEPLOYMENT with no
    error raised anywhere."""
    providers = build_tier_providers(
        _settings(
            retail_model_provider=" azure-openai",
            retail_azure_small_deployment="tiny",
            retail_azure_deployment="huge",
        )
    )
    assert providers.small.capabilities().provider_keys == (AZURE_PROVIDER_KEY,)
    assert providers.small._model_config == {"azure_deployment": "tiny"}
    assert providers.frontier._model_config == {"azure_deployment": "huge"}


def test_build_tier_providers_refuses_an_unknown_model_provider() -> None:
    from egpt.providers.model_provider import ModelProviderConfigError

    with pytest.raises(ModelProviderConfigError, match="azure-openai-typo"):
        build_tier_providers(_settings(retail_model_provider="azure-openai-typo"))


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


class _StubAgent:
    """The attributes `apply_run_model` reads: the provider, `id`, and the pre-run `model`
    (`run_model`'s fallback when a resolution is not attributed back to this component)."""

    def __init__(
        self, model_provider: object, agent_id: str = "join_path_explainer"
    ) -> None:
        self.id = agent_id
        self.model_provider = model_provider
        self.model = None


class _StubRunOutput:
    """The two attributes `_stamp_resolved_model`/`_stamp_escalation` write to."""

    def __init__(self) -> None:
        self.model = None
        self.model_provider = None
        self.metadata = None


def test_an_escalated_run_is_recorded_in_the_run_metadata_through_the_real_tier_providers() -> (
    None
):
    """Every other test in this file drives `resolve_detailed` directly. This drives a real
    `TierModelProvider` pair -- the exact composition `build_agents` wires up for a small-tier
    agent -- through `apply_run_model`, the per-run hook that writes
    `RunOutput.metadata["egpt_escalation"]`, so a regression in that write path (or in R8's
    provider-key collision resolving differently than expected) is caught here rather than
    only by the hand-run cookbook (`cookbook/94_retail/R08_model_routing.py`)."""
    from egpt.agent._run import ESCALATION_METADATA_KEY, apply_run_model
    from egpt.run.base import RunContext

    providers = build_tier_providers(_settings(retail_small_context_window=1_000))
    composed = provider_for(Tier.SMALL, providers)

    run_context = RunContext(run_id="r-1", session_id="s-1", user_id="u-1")
    run_response = _StubRunOutput()

    changed = apply_run_model(
        _StubAgent(composed),
        run_context,
        tools=None,
        run_response=run_response,
        estimated_input_tokens=1_001,
    )

    assert changed
    record = run_response.metadata[ESCALATION_METADATA_KEY]
    assert record["reason"] == "input_exceeds_context_window"
    # R8's known collision (see the PR's "Findings worth reading"): both tiers narrow to the
    # same registry key, so `escalated_from` and `target` read identically here -- `model_id`
    # is left to carry the small-vs-frontier distinction alone in the persisted record.
    assert record["escalated_from"] == AZURE_PROVIDER_KEY == record["target"]
    assert record["model_id"] == providers.frontier.model_id == run_response.model
    assert record["escalation_level"] == 1


def test_a_non_escalated_run_through_the_real_tier_providers_writes_no_record() -> None:
    """Twin of the escalated case above: a request the small tier can serve must leave the run
    record untouched, not write an empty/absent-reason entry."""
    from egpt.agent._run import ESCALATION_METADATA_KEY, apply_run_model
    from egpt.run.base import RunContext

    providers = build_tier_providers(_settings(retail_small_context_window=1_000))
    composed = provider_for(Tier.SMALL, providers)

    run_context = RunContext(run_id="r-2", session_id="s-2", user_id="u-1")
    run_response = _StubRunOutput()

    changed = apply_run_model(
        _StubAgent(composed),
        run_context,
        tools=None,
        run_response=run_response,
        estimated_input_tokens=999,
    )

    assert changed
    assert (
        run_response.metadata is None
        or ESCALATION_METADATA_KEY not in run_response.metadata
    )
    assert run_response.model == providers.small.model_id


def test_build_agents_pins_no_model_and_routes_each_agent() -> None:
    agents = {agent.id: agent for agent in _static_agents()}
    assert all(agent.model is None for agent in agents.values())
    assert isinstance(
        agents[JOIN_PATH_EXPLAINER_ID].model_provider, EscalatingModelProvider
    )
    assert isinstance(agents[SYNONYM_PROPOSER_ID].model_provider, TierModelProvider)
    assert agents[SYNONYM_PROPOSER_ID].model_provider.tier is Tier.FRONTIER


def test_build_agents_also_registers_the_playground_factory() -> None:
    """R11: not a static agent, so it is excluded from `_static_agents()` above and from
    every assertion in this module that assumes `.model`/`.initialize_agent()` -- it still
    has to have a row in the routing table, same as any other agent id."""
    agents = _agents()
    factories = [agent for agent in agents if isinstance(agent, AgentFactory)]
    assert [factory.id for factory in factories] == [PLAYGROUND_AGENT_ID]
    assert PLAYGROUND_AGENT_ID in ROUTING_TABLE


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
    agents = {agent.id: agent for agent in _static_agents(settings)}
    for agent in agents.values():
        agent.initialize_agent()
    assert agents[JOIN_PATH_EXPLAINER_ID].model.id == "tiny"
    assert agents[SYNONYM_PROPOSER_ID].model.id == "huge"


def test_every_registered_agent_is_a_memory_writer() -> None:
    """The lifespan hands every *static* agent the memory provider, so each writes a
    partition the consolidator must drain. R11's playground factory is the one registered
    agent id deliberately absent from `MEMORY_WRITING_AGENT_IDS` -- see its own docstring in
    app/components.py for why: the lifespan iterates the factory object, not the fresh
    `Agent` it builds per request, so wiring memory into it is separate, undone work."""
    assert set(MEMORY_WRITING_AGENT_IDS) == {agent.id for agent in _static_agents()}
    all_ids = {agent.id for agent in _agents()}
    assert all_ids - set(MEMORY_WRITING_AGENT_IDS) == {PLAYGROUND_AGENT_ID}


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
