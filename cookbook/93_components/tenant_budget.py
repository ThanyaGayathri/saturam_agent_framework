"""Per-tenant token budget and rate limiting (EGPT-3.6).

A budget is enforced at the model-resolution seam: the tenant is checked *before* a model is built
and the call is made, so an exhausted tenant costs nothing. Enforcement is inert until an operator
installs the provider -- that is deliberate, since installing a process-wide provider is a
deployment decision rather than something the library does on import.

This example runs the whole thing without a server, a database or a model call.

Run:
    .venvs/demo/bin/python cookbook/93_components/tenant_budget.py
"""

from egpt.exceptions import BudgetExceededError, RateLimitExceededError
from egpt.models.message import Message
from egpt.models.response import ModelResponse
from egpt.providers import BudgetAwareModelProvider, BudgetEnforcer, BudgetLimits
from egpt.providers.budget import InMemoryCounterStore
from egpt.providers.model_provider import (
    BaseModelProvider,
    ModelCapabilities,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
)


class StubResolver(BaseModelProvider):
    """Stands in for the real resolution stack so this example needs no model SDK."""

    name = "stub"

    def __init__(self) -> None:
        self.calls = 0

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        self.calls += 1
        from egpt.integrations.models.openai import OpenAIResponses

        return ModelResolveResult(
            model=OpenAIResponses(id="gpt-5.5", api_key="not-used-in-this-example"),
            mode=ProviderMode.FRONTIER,
            provider_key="openai",
            reason="stub resolution",
        )

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(modes=(ProviderMode.FRONTIER,), supports_tools=True)


def main() -> None:
    inner = StubResolver()

    # A budget of 1000 tokens per hour. In a real deployment this comes from the tenant's
    # ModelPolicy (`"budget": {"max_tokens": 1000, "window": "hour"}` in the policy document) or
    # from EGPT_BUDGET_MAX_TOKENS, and `BudgetAwareModelProvider.from_settings()` builds the stack.
    limits = BudgetLimits(max_tokens=1_000, max_requests=5, window="hour")
    enforcer = BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)
    provider = BudgetAwareModelProvider(
        inner, enforcer=enforcer, limits_for=lambda request: limits
    )

    print("-- a tenant with headroom resolves normally")
    result = provider.resolve_detailed(
        ModelResolveRequest(
            tenant_id="acme", agent_id="support-bot", estimated_input_tokens=200
        )
    )
    print("   model:", result.model.id)
    print("   usage:", enforcer.usage("acme", limits))

    print()
    print("-- usage is attributed per agent, not only per tenant")
    provider.resolve_detailed(
        ModelResolveRequest(
            tenant_id="acme", agent_id="research-bot", estimated_input_tokens=300
        )
    )
    print(
        "   support-bot :",
        enforcer.usage("acme", limits, agent_id="support-bot")["tokens"],
    )
    print(
        "   research-bot:",
        enforcer.usage("acme", limits, agent_id="research-bot")["tokens"],
    )
    print("   tenant total:", enforcer.usage("acme", limits)["tokens"])

    print()
    print("-- another tenant is unaffected")
    print("   globex usage:", enforcer.usage("globex", limits))

    print()
    print("-- every turn of a tool-calling run is counted, not just the first")
    # The seam resolves ONE model per run, but a tool-calling loop invokes it once per turn, and
    # each turn reports only its own usage in a fresh MessageMetrics. So settlement has to charge
    # each turn's actual in full rather than treating the reported number as a running total --
    # otherwise a tool-using agent is billed for roughly one turn out of N.
    turn_limits = BudgetLimits(max_tokens=100_000, window="hour")
    turn_enforcer = BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)
    turn_provider = BudgetAwareModelProvider(
        StubResolver(), enforcer=turn_enforcer, limits_for=lambda request: turn_limits
    )
    resolved = turn_provider.resolve_detailed(
        ModelResolveRequest(tenant_id="initech", estimated_input_tokens=100)
    )
    print(
        "   after the up-front estimate:",
        turn_enforcer.usage("initech", turn_limits)["tokens"],
    )

    messages = []  # a real run holds its messages; keeping them mirrors that
    for turn in (1, 2, 3):
        message = Message(role="assistant")
        resolved.model._ensure_message_metrics_initialized(message)
        message.metrics.input_tokens = 500
        message.metrics.output_tokens = 200
        messages.append(message)
        # This is the chokepoint the real tool loop calls on every iteration.
        resolved.model._populate_assistant_message(
            assistant_message=message,
            provider_response=ModelResponse(role="assistant", content="done"),
        )
        print(
            f"   after turn {turn} (700 real tokens):",
            turn_enforcer.usage("initech", turn_limits)["tokens"],
        )
    print(
        "   three turns of 700 tokens sum to 2100, and the 100-token estimate nets out once"
    )

    print()
    print("-- an exhausted tenant is refused before the model is built")
    enforcer.reserve("acme", limits, estimated_tokens=1_000)
    calls_before = inner.calls
    try:
        provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))
    except BudgetExceededError as error:
        print("   refused:", error)
        print("   retry_after:", error.retry_after, "seconds")
        print("   inner resolver consulted:", inner.calls - calls_before, "times")

    print()
    print("-- a rate cap raises a different, distinct type")
    rate_only = BudgetLimits(max_requests=1, window="minute")
    rate_enforcer = BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)
    rate_provider = BudgetAwareModelProvider(
        StubResolver(), enforcer=rate_enforcer, limits_for=lambda request: rate_only
    )
    rate_provider.resolve_detailed(ModelResolveRequest(tenant_id="globex"))
    try:
        rate_provider.resolve_detailed(ModelResolveRequest(tenant_id="globex"))
    except RateLimitExceededError as error:
        print("   throttled:", error)

    print()
    print(
        "-- to enforce this in a real deployment, install the provider once at startup:"
    )
    print(
        "       from egpt.providers import BudgetAwareModelProvider, set_default_model_provider"
    )
    print("       budgeted = BudgetAwareModelProvider.from_settings()")
    print("       if budgeted is not None:")
    print("           set_default_model_provider(budgeted)")
    print()
    print(
        "   from_settings() returns None when no budget is configured, so the no-budget"
    )
    print("   deployment installs nothing and carries no extra layer.")
    print()
    print(
        "   Note: with multiple workers, set EGPT_BUDGET_REDIS_URL. The in-memory store counts"
    )
    print("   per process, so N workers would allow N times the configured limit.")


if __name__ == "__main__":
    main()
