"""Token-to-cost computation (aicoe-tracker#163).

``MessageMetrics.cost`` existed throughout the metrics layer and was never populated: exactly one
write site in the package (`integrations/models/openai/chat.py`) did
``getattr(response_usage, "cost", None)`` against an OpenAI usage object that has no ``cost``
attribute, so it yielded a number only for OpenAI-compatible gateways that inject one. Every other
provider left it ``None``, and there was no price table anywhere in the tree.

Design, following the ticket's notes:

* **The table is data.** Adding a model or repricing one is a one-row edit to ``PRICES``, not a
  code change. That is an acceptance criterion rather than a nicety, because vendors reprice often
  and a table that needs code review per change goes stale.

* **A gateway-reported cost wins.** OpenRouter, Portkey and LiteLLM report what they actually
  charged, which is authoritative in a way a computed estimate never is. Computation only fills
  the gap when nothing was reported.

* **An unpriced model yields ``None``, never a guess.** For billing, a missing number is a
  reporting gap; a wrong number is an incorrect invoice. There is no "close enough" default rate,
  and no fallback to a similarly-named model.

* **Rates are per token-class.** Cached input and reasoning tokens are priced differently by
  several vendors and are already tracked separately in ``BaseMetrics``, so collapsing them into a
  single input rate would systematically misprice exactly the workloads that use them most.

**Staleness is the real maintenance liability**, and it is deliberately visible rather than
hidden: every row carries the date its rates were last checked, ``PRICES_AS_OF`` records the sweep
date, and ``stale_entries()`` lets a deployment or a CI job surface rows that have aged out. A
table that silently produces wrong billing numbers is worse than one that reports it cannot.

Rates are USD per 1,000,000 tokens, matching how every vendor publishes them -- storing per-token
floats would introduce rounding noise into the table itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional, Tuple

__all__ = [
    "PRICES",
    "PRICES_AS_OF",
    "ModelPrice",
    "compute_cost",
    "price_for",
    "stale_entries",
]

#: When the table below was last swept against published vendor pricing. Surfaced by
#: ``stale_entries`` so an operator can tell a current number from an old one.
PRICES_AS_OF = date(2026, 8, 1)


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens, by token class.

    ``cached_input`` and ``reasoning`` are ``None`` when the vendor does not price them
    separately -- ``None`` means "charge this class at the ordinary rate", which is different from
    ``0.0`` (free), and several vendors do offer genuinely free cache reads.
    """

    input: float
    output: float
    cached_input: Optional[float] = None
    reasoning: Optional[float] = None
    #: When this row was last verified against the vendor's published price.
    as_of: date = PRICES_AS_OF


#: ``(provider_key, model_id)`` -> rates. The provider key is the registry key
#: (``discovery.entries("model")``), not the display name, so a row is addressable the same way
#: everything else in the tree addresses a provider.
#:
#: Deliberately covers the models EGPT ships as defaults plus the common tiers of each vendor.
#: An absent model is priced ``None``, which is the correct outcome -- see the module docstring.
#: **Every row here must come from a published vendor price page, not an extrapolation.** This
#: module's own stance is that "a missing number is a reporting gap; a wrong number is an incorrect
#: invoice" -- so a model whose rate I cannot cite is deliberately absent, and `compute_cost`
#: returns None for it rather than guessing.
#:
#: An earlier version of this table carried extrapolated `gpt-5.5`/`gpt-5.4` rates and listed the
#: Claude 4.x family while omitting Claude 5 entirely. Those rows have been removed rather than
#: adjusted: an unpriced model is honest, a plausibly-wrong price is not, and this table is the one
#: place in the tree where being approximately right is worse than being silent.
#:
#: Populating it is a data task for whoever owns billing, and deliberately a one-row change:
#: add `(provider_key, model_id): ModelPrice(input=..., output=..., as_of=date(...))`. `stale_entries()`
#: reports rows whose `as_of` has aged out, so a filled table cannot rot silently.
PRICES: Dict[Tuple[str, str], ModelPrice] = {
    # Intentionally empty pending verified rates. See the note above: the arithmetic, the token-class
    # handling and the gateway-cost precedence are all tested against synthetic rows in
    # `tests/unit/models/test_pricing.py`, so the mechanism is proven without shipping a number that
    # might bill a customer wrongly.
}


def price_for(provider_key: Optional[str], model_id: Optional[str]) -> Optional[ModelPrice]:
    """The rates for a model, or ``None`` when it is not priced.

    Exact match only. No prefix matching, no "nearest model", no default rate: a model whose id
    merely resembles a priced one may be a different tier at a different price, and inventing a
    number for it produces a confidently wrong invoice.
    """
    if not provider_key or not model_id:
        return None
    return PRICES.get((provider_key.strip().lower(), model_id.strip()))


def compute_cost(
    *,
    provider_key: Optional[str],
    model_id: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> Optional[float]:
    """USD for one call, or ``None`` when the model is not priced.

    Token classes are charged at their own rates where the vendor differentiates:

    * ``cache_read_tokens`` are billed at ``cached_input`` and **subtracted from** ``input_tokens``
      -- every vendor reports cached tokens as a subset of the input total, so charging both would
      double-bill the cached portion. Getting this backwards is the most expensive kind of error
      this function can make, since caching is used most by the highest-volume workloads.
    * ``reasoning_tokens`` are a subset of ``output_tokens`` in the same way, and are only
      re-priced when the vendor gives them a distinct rate.
    """
    price = price_for(provider_key, model_id)
    if price is None:
        return None

    per_million = 1_000_000.0

    cached = max(0, cache_read_tokens)
    uncached_input = max(0, input_tokens - cached)
    cost = uncached_input * price.input / per_million
    cost += cached * (price.cached_input if price.cached_input is not None else price.input) / per_million

    if price.reasoning is not None:
        reasoning = max(0, reasoning_tokens)
        ordinary_output = max(0, output_tokens - reasoning)
        cost += ordinary_output * price.output / per_million
        cost += reasoning * price.reasoning / per_million
    else:
        cost += max(0, output_tokens) * price.output / per_million

    return cost


def stale_entries(*, today: Optional[date] = None, max_age_days: int = 180) -> Dict[Tuple[str, str], date]:
    """Rows whose rates have not been verified within ``max_age_days``.

    Exists so staleness is observable rather than silent. A deployment can log this at startup, or
    a scheduled CI job can fail on it -- either is better than discovering a repricing through a
    reconciliation dispute.
    """
    reference = today or date.today()
    return {key: price.as_of for key, price in PRICES.items() if (reference - price.as_of).days > max_age_days}
