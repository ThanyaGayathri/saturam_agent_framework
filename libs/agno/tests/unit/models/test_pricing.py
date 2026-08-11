"""Token-to-cost computation (aicoe-tracker#163).

`MessageMetrics.cost` was never populated: one write site in the whole package did a `getattr`
against an OpenAI usage object with no `cost` attribute, so every first-party provider reported
None and there was no price table anywhere in the tree.

These tests pin the four properties that make a billing number trustworthy: exact-match pricing,
no double-billing of cached tokens, a gateway-reported cost winning over a computed one, and an
unpriced model yielding None rather than a guess.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from egpt.models.pricing import PRICES, ModelPrice, compute_cost, price_for, stale_entries


@pytest.fixture(autouse=True)
def synthetic_prices(monkeypatch):
    """Install known rows for the duration of each test.

    The shipped `PRICES` table is intentionally empty until someone verifies real rates against
    published vendor pages -- this module's stance is that a wrong price is worse than a missing one.
    Testing the arithmetic against synthetic rows keeps that honest: the *mechanism* (token-class
    handling, cache subtraction, gateway precedence, exact matching) is fully covered without
    pinning any number that could bill a customer.
    """
    rows = {
        ("openai", "gpt-5.5"): ModelPrice(input=1.25, output=10.00, cached_input=0.125),
        ("aws-bedrock", "no-cache-rate"): ModelPrice(input=3.00, output=15.00),
    }
    monkeypatch.setattr("egpt.models.pricing.PRICES", rows)
    return rows


class TestExactMatchOnly:
    def test_a_priced_model_resolves(self):
        assert price_for("openai", "gpt-5.5") is not None

    def test_an_unpriced_model_is_none_not_a_guess(self):
        """For billing, a missing number is a reporting gap; a wrong number is a bad invoice."""
        assert price_for("openai", "gpt-5.5-turbo-preview-unknown") is None
        assert compute_cost(provider_key="openai", model_id="unknown", input_tokens=1_000_000) is None

    def test_a_similar_id_does_not_match_by_prefix(self):
        """`gpt-5.5-mini` may be a different tier at a different price. Inventing a rate for it
        produces a confidently wrong number, which is worse than none."""
        assert price_for("openai", "gpt-5.5-mini") is None

    def test_an_unknown_provider_is_none(self):
        assert price_for("not-a-provider", "gpt-5.5") is None

    def test_missing_identifiers_are_none(self):
        assert price_for(None, "gpt-5.5") is None
        assert price_for("openai", None) is None
        assert compute_cost(provider_key=None, model_id=None, input_tokens=100) is None

    def test_the_provider_key_is_matched_case_insensitively(self):
        """Registry keys are lowercase, but a caller may pass a display-ish value."""
        assert price_for("OpenAI", "gpt-5.5") is not None


class TestArithmetic:
    def test_input_and_output_are_priced_at_their_own_rates(self):
        # gpt-5.5: $1.25/1M in, $10.00/1M out
        cost = compute_cost(provider_key="openai", model_id="gpt-5.5", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == pytest.approx(11.25)

    def test_cached_tokens_are_not_double_billed(self):
        """Every vendor reports cached tokens as a *subset* of the input total, so charging both
        the full input rate and the cached rate bills the cached portion twice. This is the most
        expensive error this function can make -- caching is used most by the highest-volume
        workloads, so the overcharge scales with the customer."""
        cost = compute_cost(
            provider_key="openai", model_id="gpt-5.5", input_tokens=1_000_000, cache_read_tokens=1_000_000
        )

        # $0.125/1M cached, and zero uncached input remaining -- not 1.25 + 0.125.
        assert cost == pytest.approx(0.125)

    def test_a_partially_cached_request_splits_the_input(self):
        cost = compute_cost(
            provider_key="openai", model_id="gpt-5.5", input_tokens=1_000_000, cache_read_tokens=400_000
        )

        # 600k at $1.25/1M + 400k at $0.125/1M
        assert cost == pytest.approx(0.6 * 1.25 + 0.4 * 0.125)

    def test_a_vendor_without_a_cached_rate_charges_the_ordinary_input_rate(self):
        """`None` means "no separate rate", which is different from 0.0 (free)."""
        price = price_for("aws-bedrock", "no-cache-rate")
        assert price is not None and price.cached_input is None

        cost = compute_cost(
            provider_key="aws-bedrock",
            model_id="no-cache-rate",
            input_tokens=1_000_000,
            cache_read_tokens=1_000_000,
        )

        assert cost == pytest.approx(3.00)

    def test_zero_tokens_cost_nothing(self):
        assert compute_cost(provider_key="openai", model_id="gpt-5.5") == pytest.approx(0.0)

    def test_negative_counts_are_clamped_rather_than_credited(self):
        """A malformed usage payload must not produce a negative charge."""
        cost = compute_cost(provider_key="openai", model_id="gpt-5.5", input_tokens=-5, output_tokens=-5)
        assert cost == pytest.approx(0.0)

    def test_cached_tokens_exceeding_the_input_total_do_not_go_negative(self):
        cost = compute_cost(provider_key="openai", model_id="gpt-5.5", input_tokens=100, cache_read_tokens=1_000)
        assert cost >= 0

    def test_reasoning_tokens_are_repriced_only_when_the_vendor_differentiates(self, synthetic_prices):
        """Reasoning tokens are a subset of output, like cached is of input."""
        # setitem on the fixture dict: `synthetic_prices` already replaced the module attribute,
        # so patching that attribute again here would shadow it and lose the other rows.
        synthetic_prices[("test", "reasoner")] = ModelPrice(input=1.0, output=10.0, reasoning=30.0)

        cost = compute_cost(provider_key="test", model_id="reasoner", output_tokens=1_000_000, reasoning_tokens=400_000)

        # 600k ordinary output at $10/1M + 400k reasoning at $30/1M
        assert cost == pytest.approx(0.6 * 10.0 + 0.4 * 30.0)


class TestStaleness:
    def test_a_fresh_table_reports_nothing_stale(self):
        assert stale_entries(today=date(2026, 8, 2), max_age_days=180) == {}

    def test_aged_rows_are_surfaced(self):
        """Staleness must be observable: vendors reprice, and a stale table produces wrong billing
        numbers silently. Better to be told than to discover it in a reconciliation dispute."""
        far_future = date(2026, 8, 1) + timedelta(days=400)

        stale = stale_entries(today=far_future, max_age_days=180)

        assert stale, "every row should be reported stale at +400 days"
        assert ("openai", "gpt-5.5") in stale


class TestTheTableIsData:
    def test_every_row_is_a_model_price_with_sane_rates(self):
        """Adding or repricing a model must be a one-row data change, so the table's shape is the
        contract: a malformed row is a typo, and a typo in a price table is a billing error."""
        for key, price in PRICES.items():
            provider, model_id = key
            assert isinstance(provider, str) and provider, key
            assert isinstance(model_id, str) and model_id, key
            assert price.input >= 0 and price.output >= 0, key
            if price.cached_input is not None:
                assert price.cached_input <= price.input, f"{key}: a cache read should not cost more than a fresh read"

    def test_provider_keys_are_lowercase_registry_keys(self):
        """The table is addressed by registry key, the same way everything else addresses a
        provider -- not by display name."""
        for provider, _ in PRICES:
            assert provider == provider.lower(), provider


class TestIntegrationWithTheMetricsLayer:
    """The hook lives in `Model._populate_assistant_message`, which every provider funnels
    through -- one edit rather than 46, and a new provider gets costing for free."""

    def _model(self):
        pytest.importorskip("openai", reason="OpenAIResponses needs the openai SDK")
        from egpt.integrations.models.openai.responses import OpenAIResponses

        return OpenAIResponses(id="gpt-5.5", api_key="test")

    def test_cost_is_populated_from_real_token_counts(self):
        from egpt.models.message import Message
        from egpt.models.metrics import MessageMetrics
        from egpt.models.response import ModelResponse

        model = self._model()
        message = Message(role="assistant")

        model._populate_assistant_message(
            assistant_message=message,
            provider_response=ModelResponse(
                content="hi", response_usage=MessageMetrics(input_tokens=1_000, output_tokens=500)
            ),
        )

        # 1000 @ $1.25/1M + 500 @ $10/1M
        assert message.metrics.cost == pytest.approx(0.00625)

    def test_a_gateway_reported_cost_is_never_overwritten(self):
        """OpenRouter/Portkey/LiteLLM report what they actually charged, which is authoritative in
        a way a computed estimate is not."""
        from egpt.models.message import Message
        from egpt.models.metrics import MessageMetrics
        from egpt.models.response import ModelResponse

        model = self._model()
        message = Message(role="assistant")
        usage = MessageMetrics(input_tokens=1_000, output_tokens=500)
        usage.cost = 0.99

        model._populate_assistant_message(
            assistant_message=message,
            provider_response=ModelResponse(content="hi", response_usage=usage),
        )

        assert message.metrics.cost == 0.99

    def test_an_unpriced_model_leaves_cost_none(self):
        from egpt.models.message import Message
        from egpt.models.metrics import MessageMetrics
        from egpt.models.response import ModelResponse

        pytest.importorskip("openai", reason="OpenAIResponses needs the openai SDK")
        from egpt.integrations.models.openai.responses import OpenAIResponses

        model = OpenAIResponses(id="some-unreleased-model", api_key="test")
        message = Message(role="assistant")

        model._populate_assistant_message(
            assistant_message=message,
            provider_response=ModelResponse(
                content="hi", response_usage=MessageMetrics(input_tokens=1_000, output_tokens=500)
            ),
        )

        assert message.metrics.cost is None

    def test_costing_never_fails_a_call(self, monkeypatch):
        """Costing is reporting, not serving. A pricing failure must not take down a run that
        otherwise succeeded -- the same principle as the input-token estimate in agent/_run.py.

        Faults `compute_cost` itself rather than feeding a malformed payload: a non-numeric token
        count crashes in `MessageMetrics.__iadd__` (`metrics.py:198`) *before* costing is reached,
        so that route would test the metrics layer's fragility rather than this guard.
        """
        from egpt.models.message import Message
        from egpt.models.metrics import MessageMetrics
        from egpt.models.response import ModelResponse

        def exploding_compute_cost(**kwargs):
            raise RuntimeError("price table unavailable")

        monkeypatch.setattr("egpt.models.pricing.compute_cost", exploding_compute_cost)

        model = self._model()
        message = Message(role="assistant")

        model._populate_assistant_message(
            assistant_message=message,
            provider_response=ModelResponse(
                content="hi", response_usage=MessageMetrics(input_tokens=1_000, output_tokens=500)
            ),
        )

        assert message.content == "hi", "the call survives"
        assert message.metrics.cost is None, "only the cost is missing"
        assert message.metrics.input_tokens == 1_000, "token accounting is unaffected"
