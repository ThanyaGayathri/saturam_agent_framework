"""Rule-based escalation (EGPT-3.5).

Every test here runs with zero I/O. The rules are pure functions of the request and the
primary's capabilities, and the two providers under test are file-local fakes -- never the Mode
A/B bindings, which land in sibling tickets (#65/#66) and are not on this branch. That is the
point of composing two injected ``ModelProvider`` instances rather than importing concrete
providers: the decision is testable without a network, an SDK, or a model server.
"""

from __future__ import annotations

import ast
import functools
import inspect
import logging
from pathlib import Path
from typing import Optional

import pytest

from egpt.models.base import Model
from egpt.providers.escalation import (
    CAPABILITY_RULES,
    DEFAULT_RULES,
    ESCALATE_METADATA_KEY,
    EscalatingModelProvider,
    explicit_policy_flag,
    input_exceeds_context_window,
    structured_output_unsupported,
    too_many_tools,
    tool_count_over,
    tools_unsupported,
    vision_unsupported,
)
from egpt.providers.model_provider import (
    ModelCapabilities,
    ModelProvider,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
)

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeModel(Model):
    """A Model that can be constructed without an SDK and never makes a call.

    ``Model`` is an ABC whose abstract surface is the response methods; escalation only ever
    holds the instance and reads ``.id``, so the bodies stay unreachable.
    """

    def invoke(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("FakeModel must not be invoked")

    async def ainvoke(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("FakeModel must not be invoked")

    def invoke_stream(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("FakeModel must not be invoked")

    async def ainvoke_stream(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("FakeModel must not be invoked")

    def _parse_provider_response(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("FakeModel must not be invoked")

    def _parse_provider_response_delta(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("FakeModel must not be invoked")


class FakeProvider:
    """A ``ModelProvider`` that records what it was asked to resolve.

    Satisfies the Protocol by shape alone -- no inheritance -- which is exactly the property
    ``model_provider.py`` chose a Protocol for.
    """

    def __init__(
        self,
        name: str,
        capabilities: Optional[ModelCapabilities] = None,
        mode: ProviderMode = ProviderMode.LOCAL,
    ) -> None:
        self.name = name
        self._capabilities = capabilities or ModelCapabilities()
        self._mode = mode
        self.calls: list[ModelResolveRequest] = []

    def resolve(self, request: ModelResolveRequest) -> Model:
        self.calls.append(request)
        return FakeModel(id=f"{self.name}-model")

    def capabilities(self) -> ModelCapabilities:
        return self._capabilities


class FakeDetailedProvider(FakeProvider):
    """A provider that also implements the optional ``resolve_detailed``."""

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        model = self.resolve(request)
        caps = self.capabilities()
        return ModelResolveResult(
            model=model,
            mode=self._mode,
            provider_key=caps.provider_keys[0] if caps.provider_keys else self.name,
            adapter=request.adapter,
            gateway=caps.gateway,
        )


SMALL = ModelCapabilities(
    modes=(ProviderMode.LOCAL,),
    provider_keys=("vllm",),
    supports_vision=False,
    context_window=8_192,
    egress_free=True,
)

FRONTIER = ModelCapabilities(
    modes=(ProviderMode.FRONTIER,),
    provider_keys=("anthropic",),
    supports_vision=True,
    supports_escalation=True,
    context_window=200_000,
)


@pytest.fixture
def primary() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)


@pytest.fixture
def frontier() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-b", FRONTIER, ProviderMode.FRONTIER)


@pytest.fixture
def provider(primary: FakeDetailedProvider, frontier: FakeDetailedProvider) -> EscalatingModelProvider:
    return EscalatingModelProvider(primary=primary, escalation_target=frontier)


# --------------------------------------------------------------------------- #
# Contract conformance
# --------------------------------------------------------------------------- #


class TestContractConformance:
    def test_satisfies_model_provider_protocol(self, provider):
        assert isinstance(provider, ModelProvider)

    def test_fakes_satisfy_the_protocol(self, primary, frontier):
        """If the fakes did not, the tests below would prove nothing about real providers."""
        assert isinstance(primary, ModelProvider)
        assert isinstance(frontier, ModelProvider)

    def test_has_a_stable_name(self, provider):
        assert provider.name == "escalating"

    def test_resolve_returns_a_model(self, provider):
        assert isinstance(provider.resolve(ModelResolveRequest()), Model)

    def test_reports_escalation_support(self, provider):
        assert provider.capabilities().supports_escalation is True


# --------------------------------------------------------------------------- #
# Rules, as pure functions
# --------------------------------------------------------------------------- #


class TestRules:
    def test_input_over_context_window_fires(self):
        request = ModelResolveRequest(estimated_input_tokens=20_000)
        assert input_exceeds_context_window(request, SMALL) == "input_exceeds_context_window"

    def test_input_within_context_window_does_not_fire(self):
        request = ModelResolveRequest(estimated_input_tokens=1_000)
        assert input_exceeds_context_window(request, SMALL) is None

    def test_input_exactly_at_context_window_does_not_fire(self):
        """The window is inclusive: a request that exactly fits, fits."""
        request = ModelResolveRequest(estimated_input_tokens=8_192)
        assert input_exceeds_context_window(request, SMALL) is None

    def test_unknown_token_count_does_not_fire(self):
        """None means unknown, and the contract forbids routing on an unknown signal."""
        request = ModelResolveRequest(estimated_input_tokens=None)
        assert input_exceeds_context_window(request, SMALL) is None

    def test_unknown_context_window_does_not_fire(self):
        """A vLLM deployment cannot always state its window; a guess must not escalate."""
        unknown = ModelCapabilities(context_window=None)
        request = ModelResolveRequest(estimated_input_tokens=1_000_000)
        assert input_exceeds_context_window(request, unknown) is None

    def test_vision_against_non_vision_primary_fires(self):
        assert vision_unsupported(ModelResolveRequest(requires_vision=True), SMALL) == "vision_unsupported"

    def test_vision_against_vision_primary_does_not_fire(self):
        assert vision_unsupported(ModelResolveRequest(requires_vision=True), FRONTIER) is None

    def test_no_vision_required_does_not_fire(self):
        assert vision_unsupported(ModelResolveRequest(requires_vision=False), SMALL) is None

    def test_tools_against_toolless_primary_fires(self):
        toolless = ModelCapabilities(supports_tools=False)
        assert tools_unsupported(ModelResolveRequest(tool_count=1), toolless) == "tools_unsupported"

    def test_tools_against_tool_capable_primary_does_not_fire(self):
        assert tools_unsupported(ModelResolveRequest(tool_count=3), SMALL) is None

    def test_structured_output_against_incapable_primary_fires(self):
        plain = ModelCapabilities(supports_structured_output=False)
        request = ModelResolveRequest(requires_structured_output=True)
        assert structured_output_unsupported(request, plain) == "structured_output_unsupported"

    def test_structured_output_against_capable_primary_does_not_fire(self):
        request = ModelResolveRequest(requires_structured_output=True)
        assert structured_output_unsupported(request, SMALL) is None

    def test_tool_count_over_threshold_fires(self):
        assert too_many_tools(ModelResolveRequest(tool_count=50), SMALL) == "too_many_tools"

    def test_tool_count_under_threshold_does_not_fire(self):
        assert too_many_tools(ModelResolveRequest(tool_count=2), SMALL) is None

    def test_explicit_policy_flag_fires(self):
        request = ModelResolveRequest(metadata={ESCALATE_METADATA_KEY: True})
        assert explicit_policy_flag(request, SMALL) == "explicit_policy_flag"

    def test_absent_policy_flag_does_not_fire(self):
        assert explicit_policy_flag(ModelResolveRequest(), SMALL) is None

    def test_falsy_policy_flag_does_not_fire(self):
        request = ModelResolveRequest(metadata={ESCALATE_METADATA_KEY: False})
        assert explicit_policy_flag(request, SMALL) is None

    def test_rules_are_pure(self):
        """A rule must not mutate the request. The contract requires it of providers, and a
        rule is where an accidental mutation would be easiest to write."""
        request = ModelResolveRequest(estimated_input_tokens=20_000, requires_vision=True, tool_count=99)
        before = (request.escalation_level, request.escalation_reason, request.requested)
        for rule in DEFAULT_RULES:
            rule(request, SMALL)
        assert (request.escalation_level, request.escalation_reason, request.requested) == before

    def test_capability_rules_are_a_subset_of_the_defaults(self):
        """The split drives the ceiling warning, so a capability rule dropped from the defaults
        would silently stop being checked at all."""
        assert set(CAPABILITY_RULES) <= set(DEFAULT_RULES)

    def test_the_tunable_rule_is_not_a_capability_rule(self):
        """A tool-count threshold is a heuristic; treating it as a hard gap would warn on a
        request the primary can in fact serve."""
        assert too_many_tools not in CAPABILITY_RULES


# --------------------------------------------------------------------------- #
# should_escalate: the decision
# --------------------------------------------------------------------------- #


class TestShouldEscalate:
    def test_non_eligible_request_stays_on_primary(self, provider):
        assert provider.should_escalate(ModelResolveRequest()) is None

    def test_oversized_input_escalates(self, provider):
        request = ModelResolveRequest(estimated_input_tokens=20_000)
        assert provider.should_escalate(request) == "input_exceeds_context_window"

    def test_vision_escalates(self, provider):
        assert provider.should_escalate(ModelResolveRequest(requires_vision=True)) == "vision_unsupported"

    def test_at_max_escalations_does_not_escalate(self, provider):
        """The ceiling is terminal, not an error: the frontier tier is the last stop."""
        request = ModelResolveRequest(requires_vision=True, escalation_level=1, max_escalations=1)
        assert request.can_escalate is False
        assert provider.should_escalate(request) is None

    def test_max_escalations_zero_never_escalates(self, provider):
        request = ModelResolveRequest(requires_vision=True, max_escalations=0)
        assert provider.should_escalate(request) is None

    def test_capability_gap_outranks_tool_threshold(self, provider):
        """The reason logged must be why the primary was actually unusable."""
        request = ModelResolveRequest(requires_vision=True, tool_count=99)
        assert provider.should_escalate(request) == "vision_unsupported"

    def test_tool_threshold_is_per_instance(self, primary, frontier):
        strict = EscalatingModelProvider(primary=primary, escalation_target=frontier, tool_threshold=1)
        assert strict.should_escalate(ModelResolveRequest(tool_count=2)) == "too_many_tools"
        assert strict.should_escalate(ModelResolveRequest(tool_count=1)) is None

    def test_threshold_survives_a_wrapped_rule(self, primary, frontier):
        """The threshold is bound into the rule, so a wrapped or aliased rule keeps it.

        An identity check against the module-level rule would silently fall back to the default
        here, ignoring the threshold the deployment configured.
        """
        strict = EscalatingModelProvider(
            primary=primary,
            escalation_target=frontier,
            rules=(functools.partial(tool_count_over(1)),),
        )
        assert strict.should_escalate(ModelResolveRequest(tool_count=2)) == "too_many_tools"

    def test_tool_count_over_builds_independent_rules(self):
        loose, tight = tool_count_over(10), tool_count_over(1)
        request = ModelResolveRequest(tool_count=5)
        assert loose(request, SMALL) is None
        assert tight(request, SMALL) == "too_many_tools"

    def test_custom_rule_set_is_honoured(self, primary, frontier):
        """A deployment may narrow the rules; only what it listed then fires."""
        vision_only = EscalatingModelProvider(primary=primary, escalation_target=frontier, rules=(vision_unsupported,))
        assert vision_only.should_escalate(ModelResolveRequest(estimated_input_tokens=20_000)) is None
        assert vision_only.should_escalate(ModelResolveRequest(requires_vision=True)) == "vision_unsupported"

    def test_empty_rule_set_never_escalates(self, primary, frontier):
        never = EscalatingModelProvider(primary=primary, escalation_target=frontier, rules=())
        assert never.should_escalate(ModelResolveRequest(requires_vision=True)) is None


# --------------------------------------------------------------------------- #
# resolve / resolve_detailed: the routing
# --------------------------------------------------------------------------- #


class TestResolution:
    def test_non_eligible_request_resolves_through_primary(self, provider, primary, frontier):
        model = provider.resolve(ModelResolveRequest())
        assert model.id == "mode-a-model"
        assert len(primary.calls) == 1
        assert frontier.calls == []

    def test_eligible_request_resolves_through_frontier(self, provider, primary, frontier):
        model = provider.resolve(ModelResolveRequest(requires_vision=True))
        assert model.id == "mode-b-model"
        assert len(frontier.calls) == 1
        assert primary.calls == []

    def test_result_records_escalated_from_and_reason(self, provider):
        result = provider.resolve_detailed(ModelResolveRequest(requires_vision=True))
        assert result.escalated_from == "vllm"
        assert result.reason == "vision_unsupported"
        assert result.provider_key == "anthropic"
        assert result.mode is ProviderMode.FRONTIER

    def test_non_escalated_result_carries_no_escalation_marks(self, provider):
        result = provider.resolve_detailed(ModelResolveRequest())
        assert result.escalated_from is None
        assert result.reason is None
        assert result.mode is ProviderMode.LOCAL

    def test_escalated_request_reaches_the_target_one_level_higher(self, provider, frontier):
        provider.resolve(ModelResolveRequest(requires_vision=True))
        seen = frontier.calls[0]
        assert seen.escalation_level == 1
        assert seen.escalation_reason == "vision_unsupported"

    def test_escalation_clears_the_pinned_model(self, provider, frontier):
        """``requested`` is what proved insufficient; re-pinning it would void the escalation."""
        pinned = FakeModel(id="too-small")
        provider.resolve(ModelResolveRequest(requested=pinned, requires_vision=True))
        assert frontier.calls[0].requested is None

    def test_resolve_does_not_mutate_the_caller_request(self, provider):
        request = ModelResolveRequest(requires_vision=True)
        provider.resolve(request)
        assert request.escalation_level == 0
        assert request.escalation_reason is None

    def test_provider_without_resolve_detailed_is_supported(self, frontier):
        """``resolve_detailed`` is optional in the contract; a bare provider must still work."""
        bare = FakeProvider("bare", SMALL)
        escalating = EscalatingModelProvider(primary=bare, escalation_target=frontier)
        result = escalating.resolve_detailed(ModelResolveRequest())
        assert result.model.id == "bare-model"
        assert result.provider_key == "vllm"
        assert result.mode is ProviderMode.LOCAL

    def test_bare_provider_as_escalation_target(self, primary):
        bare = FakeProvider("bare-frontier", FRONTIER, ProviderMode.FRONTIER)
        escalating = EscalatingModelProvider(primary=primary, escalation_target=bare)
        result = escalating.resolve_detailed(ModelResolveRequest(requires_vision=True))
        assert result.model.id == "bare-frontier-model"
        assert result.escalated_from == "vllm"
        assert result.reason == "vision_unsupported"

    def test_bare_provider_without_modes_is_reported_local(self, frontier):
        """Guessing FRONTIER would label in-VPC traffic as metered egress.

        ``modes`` defaults to empty and a provider need not fill it, so the default must be the
        less damaging of the two possible errors for cost and egress reporting.
        """
        bare = FakeProvider("in-vpc", ModelCapabilities(egress_free=True))
        escalating = EscalatingModelProvider(primary=bare, escalation_target=frontier)
        assert escalating.resolve_detailed(ModelResolveRequest()).mode is ProviderMode.LOCAL

    def test_adapter_is_not_echoed_onto_a_non_adapter_provider(self, primary):
        """A vLLM LoRA name must not be reported as running on the frontier tier."""
        bare = FakeProvider("bare-frontier", FRONTIER, ProviderMode.FRONTIER)
        escalating = EscalatingModelProvider(primary=primary, escalation_target=bare)
        result = escalating.resolve_detailed(ModelResolveRequest(requires_vision=True, adapter="lora-a"))
        assert result.adapter is None

    def test_adapter_is_reported_when_the_provider_supports_adapters(self, frontier):
        caps = ModelCapabilities(modes=(ProviderMode.LOCAL,), supports_adapters=True)
        bare = FakeProvider("vllm-bare", caps)
        escalating = EscalatingModelProvider(primary=bare, escalation_target=frontier)
        result = escalating.resolve_detailed(ModelResolveRequest(adapter="lora-a"))
        assert result.adapter == "lora-a"

    def test_capability_gap_at_the_ceiling_warns(self, provider, caplog):
        """A stranded request the primary provably cannot serve must not pass in silence."""
        caplog.set_level(logging.WARNING, logger="egpt")
        request = ModelResolveRequest(requires_vision=True, escalation_level=1, max_escalations=1)
        result = provider.resolve_detailed(request)
        assert result.provider_key == "vllm"
        assert any("vision_unsupported" in r.getMessage() for r in caplog.records)

    def test_tunable_threshold_at_the_ceiling_does_not_warn(self, provider, caplog):
        """The tool threshold is a heuristic, not a capability gap: staying is a real answer."""
        caplog.set_level(logging.WARNING, logger="egpt")
        request = ModelResolveRequest(tool_count=99, escalation_level=1, max_escalations=1)
        provider.resolve_detailed(request)
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_no_warning_when_nothing_fires(self, provider, caplog):
        caplog.set_level(logging.WARNING, logger="egpt")
        provider.resolve_detailed(ModelResolveRequest())
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# --------------------------------------------------------------------------- #
# capabilities
# --------------------------------------------------------------------------- #


class TestCapabilities:
    def test_vision_is_the_union(self, provider):
        """A vision request escalates and is served, so reporting False would be a lie."""
        assert provider.capabilities().supports_vision is True

    def test_context_window_is_the_max(self, provider):
        assert provider.capabilities().context_window == 200_000

    def test_egress_free_requires_both_tiers(self, provider):
        """A request may leave the VPC via the escalation target."""
        assert provider.capabilities().egress_free is False

    def test_egress_free_when_both_tiers_are(self, primary):
        local_target = FakeDetailedProvider("mode-a2", ModelCapabilities(egress_free=True), ProviderMode.LOCAL)
        escalating = EscalatingModelProvider(primary=primary, escalation_target=local_target)
        assert escalating.capabilities().egress_free is True

    def test_streaming_requires_both_tiers(self, primary):
        """Streaming is relied upon, not merely wanted: a non-streaming tier breaks the call."""
        no_stream = FakeDetailedProvider(
            "no-stream", ModelCapabilities(supports_streaming=False), ProviderMode.FRONTIER
        )
        escalating = EscalatingModelProvider(primary=primary, escalation_target=no_stream)
        assert escalating.capabilities().supports_streaming is False

    def test_modes_cover_both_tiers(self, provider):
        assert set(provider.capabilities().modes) == {ProviderMode.LOCAL, ProviderMode.FRONTIER}

    def test_capabilities_performs_no_resolution(self, provider, primary, frontier):
        provider.capabilities()
        assert primary.calls == []
        assert frontier.calls == []

    def test_unknown_context_windows_stay_unknown(self):
        blank = FakeDetailedProvider("x", ModelCapabilities())
        blank2 = FakeDetailedProvider("y", ModelCapabilities())
        escalating = EscalatingModelProvider(primary=blank, escalation_target=blank2)
        assert escalating.capabilities().context_window is None


# --------------------------------------------------------------------------- #
# Failure fallback reuses egpt/models/fallback.py
# --------------------------------------------------------------------------- #


class TestFallbackComposition:
    def test_to_fallback_config_holds_the_frontier_model(self, provider):
        from egpt.models.fallback import FallbackConfig

        config = provider.to_fallback_config()
        assert isinstance(config, FallbackConfig)
        assert [m.id for m in config.on_error] == ["mode-b-model"]
        assert config.has_fallbacks is True

    def test_retryable_error_returns_the_frontier_model(self, provider):
        from egpt.exceptions import ModelProviderError
        from egpt.models.fallback import get_fallback_models

        config = provider.to_fallback_config()
        models = get_fallback_models(config, ModelProviderError("boom", status_code=500))
        assert models is not None
        assert [m.id for m in models] == ["mode-b-model"]

    def test_rate_limit_returns_the_frontier_model(self, provider):
        from egpt.exceptions import ModelRateLimitError
        from egpt.models.fallback import get_fallback_models

        config = provider.to_fallback_config()
        models = get_fallback_models(config, ModelRateLimitError("429", status_code=429))
        assert [m.id for m in models] == ["mode-b-model"]

    def test_context_overflow_returns_the_frontier_model(self, provider):
        """The up-front rule misses this whenever estimated_input_tokens was None."""
        from egpt.exceptions import ContextWindowExceededError
        from egpt.models.fallback import get_fallback_models

        config = provider.to_fallback_config()
        models = get_fallback_models(config, ContextWindowExceededError("too long"))
        assert [m.id for m in models] == ["mode-b-model"]

    def test_non_retryable_client_error_is_not_masked(self, provider):
        """Reusing the existing machinery means inheriting its refusal to hide a 401."""
        from egpt.exceptions import ModelProviderError
        from egpt.models.fallback import get_fallback_models

        config = provider.to_fallback_config()
        assert get_fallback_models(config, ModelProviderError("bad key", status_code=401)) is None

    def test_fallback_config_resolves_the_escalated_request(self, provider, frontier):
        """The frontier model must not inherit the primary tier's pin or adapter."""
        pinned = FakeModel(id="small")
        provider.to_fallback_config(ModelResolveRequest(requested=pinned, adapter="lora-a"))
        assert frontier.calls[0].requested is None
        assert frontier.calls[0].adapter is None

    def test_fallback_strips_the_adapter_at_the_ceiling_too(self, provider, frontier):
        """A vLLM LoRA name sent to a frontier provider is an unknown-model error.

        The ceiling is exactly where a helper that only strips via ``escalated`` would leak it.
        """
        request = ModelResolveRequest(requested=FakeModel(id="small"), adapter="lora-a", max_escalations=0)
        provider.to_fallback_config(request)
        assert frontier.calls[0].adapter is None
        assert frontier.calls[0].requested is None

    def test_each_fallback_list_holds_its_own_model_instance(self, provider):
        """``Model`` is mutable and orchestration assigns model_type to it, so sharing one
        instance across the three lists would let one path's mutation surface on another."""
        config = provider.to_fallback_config()
        assert config.on_error[0] is not config.on_rate_limit[0]
        assert config.on_error[0] is not config.on_context_overflow[0]
        assert config.on_rate_limit[0] is not config.on_context_overflow[0]

    def test_escalation_and_fallback_are_independent(self, provider, primary, frontier):
        """Escalation is an up-front decision; fallback reacts to a failure. A request that
        does not escalate still gets a fallback config pointing at the frontier tier."""
        assert provider.should_escalate(ModelResolveRequest()) is None
        assert [m.id for m in provider.to_fallback_config().on_error] == ["mode-b-model"]


# --------------------------------------------------------------------------- #
# The v2 boundary: no confidence routing
# --------------------------------------------------------------------------- #

# Fields on ModelResolveRequest a rule may read. Everything else on the request either is, or
# could carry, prompt content -- and reading it would make the rule a confidence estimator.
_ALLOWED_REQUEST_READS = frozenset(
    {
        "estimated_input_tokens",
        "tool_count",
        "requires_structured_output",
        "requires_vision",
        "metadata",
        "can_escalate",
        "escalation_level",
        "max_escalations",
        "mode",
        "role",
        "adapter",
    }
)

# Names that would only appear in a rule if it had started reading the conversation.
_CONTENT_ATTRIBUTES = frozenset(
    {
        "messages",
        "message",
        "content",
        "prompt",
        "input",
        "text",
        "history",
        "confidence",
        "embedding",
        "logprobs",
        "perplexity",
    }
)


class TestNoConfidenceRouting:
    """v1 is deterministic and structural. Confidence-based routing is deferred to v2 because
    confidence estimation is unsolved, and a wrong guess degrades answers silently rather than
    failing loudly. These tests make that boundary mechanical rather than documentary."""

    def test_no_rule_reads_message_content(self):
        """No rule may touch a content-bearing or confidence-shaped attribute."""
        for rule in DEFAULT_RULES:
            tree = ast.parse(inspect.getsource(rule).lstrip())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    assert node.attr not in _CONTENT_ATTRIBUTES, (
                        f"rule '{rule.__name__}' reads '{node.attr}', which is confidence "
                        "routing -- out of scope for v1 (see EGPT-3.5)"
                    )

    def test_rules_read_only_structural_request_fields(self):
        """A whitelist, so a *new* content field on the request cannot be quietly read."""
        for rule in DEFAULT_RULES:
            tree = ast.parse(inspect.getsource(rule).lstrip())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id == "request":
                        assert node.attr in _ALLOWED_REQUEST_READS, (
                            f"rule '{rule.__name__}' reads request.{node.attr}, which is not a "
                            "structural routing signal"
                        )

    def test_escalation_module_names_no_content_type(self):
        """The module must not import Message or any prompt-carrying type."""
        source = Path(inspect.getfile(EscalatingModelProvider)).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "message" not in node.module, "escalation must not import the message layer"

    def test_rules_take_only_request_and_capabilities(self):
        """A rule that needed the messages would have to grow a parameter to receive them."""
        for rule in DEFAULT_RULES:
            params = list(inspect.signature(rule).parameters)
            assert params == ["request", "caps"], f"rule '{rule.__name__}' has signature {params}"

    def test_escalation_module_imports_no_concrete_provider(self):
        """ADR 0001: providers/ may not name an integration at module scope."""
        source = Path(inspect.getfile(EscalatingModelProvider)).read_text()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("egpt.integrations"), (
                    f"module-scope import of an integration: {node.module}"
                )
