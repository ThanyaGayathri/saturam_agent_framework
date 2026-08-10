"""Token/latency/cost accounting for a run.

The cost model mirrors the sibling ``benchmarks/memory_consolidation`` harness: a
configurable price table (USD per 1M tokens) with placeholder defaults that a
real run overrides via ``HARNESS_PRICE_IN`` / ``HARNESS_PRICE_OUT`` once the
gpt-5.4/5.5 Azure rates are known. Cost is reported as a *measured* number
(tokens actually consumed x price), never a cited one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

# Placeholder rates (USD per 1M tokens). Override per run; documented in PLAN.md.
_DEFAULT_PRICE_IN = float(os.getenv("HARNESS_PRICE_IN", "1.25"))
_DEFAULT_PRICE_OUT = float(os.getenv("HARNESS_PRICE_OUT", "10.0"))


@dataclass
class UsageStats:
    """Accumulates LLM token spend, call count and per-call latency across a run."""

    input_tokens: int = 0
    output_tokens: int = 0
    n_calls: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    price_in: float = _DEFAULT_PRICE_IN
    price_out: float = _DEFAULT_PRICE_OUT

    def add(self, input_tokens: int, output_tokens: int, latency_ms: float) -> None:
        self.input_tokens += int(input_tokens)
        self.output_tokens += int(output_tokens)
        self.n_calls += 1
        self.latencies_ms.append(float(latency_ms))

    def merge(self, other: "UsageStats") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.n_calls += other.n_calls
        self.latencies_ms.extend(other.latencies_ms)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        """Measured USD cost = in/1e6*price_in + out/1e6*price_out."""
        return self.input_tokens / 1e6 * self.price_in + self.output_tokens / 1e6 * self.price_out

    def to_dict(self) -> dict:
        return {
            "n_calls": self.n_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "price_in_per_1m": self.price_in,
            "price_out_per_1m": self.price_out,
        }
