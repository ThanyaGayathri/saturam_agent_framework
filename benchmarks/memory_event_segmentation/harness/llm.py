"""LLM / on-prem SLM boundary detection.

The LLM-decided segmentation option. One model call per stream reads the numbered
conversation and returns the turn indices where a new topic (episode) begins. A
single call per stream — not one per gap — is both cheaper and how this would be
deployed in practice.

Two providers, selected by ``provider``:

- ``azure_responses`` — Azure OpenAI **Responses API** via the ``openai`` SDK,
  matching the production ``OpenAIResponses`` path. Reads ``AZURE_OPENAI_*`` from
  the environment (or a gitignored ``.env``). Model id defaults to ``gpt-5.5``
  per the repo rule; the actual Azure *deployment name* is what routes the call.
- ``ollama`` — a local, on-prem SLM via the ollama HTTP API. No key, no budget;
  this is the on-prem alternative the task asks us to consider.

Latency and token usage are measured on the real call; cost is derived from
provisional unit prices (stated in DECISION.md, flagged for SM-R1). Sync
``detect`` and async ``adetect`` are both provided.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from harness.stream import Stream

_PROMPT_SYSTEM = (
    "You segment a conversation into distinct topical episodes. You are given the "
    "messages in order, each with an index. Identify every index at which a NEW "
    "topic/episode begins (the first message of a new subject). Ignore brief "
    "acknowledgements or one-off asides that stay within the current topic. Index "
    "0 is never a boundary. Respond with ONLY compact JSON of the form "
    '{"boundaries": [i, j, ...]} with the indices in ascending order.'
)


def load_env() -> None:
    """Load a gitignored .env next to the benchmark, if python-dotenv is present."""
    try:
        from dotenv import load_dotenv

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        load_dotenv(os.path.join(here, ".env"))
    except Exception:
        pass


@dataclass
class LLMResult:
    provider: str
    model_label: str
    boundaries: List[int]
    latency_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    raw: str = ""


@dataclass
class LLMBoundaryDetector:
    """One model, reused across all streams (never constructed inside a loop)."""

    provider: str = "azure_responses"
    model_label: str = "gpt-5.5"  # reporting label; Azure routes by deployment name
    # Provisional unit prices (USD per 1M tokens) — flagged for SM-R1 to ratify.
    price_in_per_m: float = 5.0
    price_out_per_m: float = 15.0
    # ollama defaults (on-prem SLM alternative).
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1:8b"))

    _client: Optional[object] = None
    _aclient: Optional[object] = None

    def _render(self, stream: Stream) -> str:
        lines = [f"{e.idx} [{e.role}] {e.text}" for e in stream.events]
        return "Conversation:\n" + "\n".join(lines)

    def _cost(self, in_tok: int, out_tok: int) -> float:
        return in_tok / 1e6 * self.price_in_per_m + out_tok / 1e6 * self.price_out_per_m

    def _parse(self, text: str, n_events: int) -> List[int]:
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            payload = json.loads(match.group(0)) if match else json.loads(text)
            raw = payload.get("boundaries", [])
            out = sorted({int(b) for b in raw if 0 < int(b) < n_events})
            return out
        except Exception:
            # Fallback: pull any integers in range.
            nums = sorted({int(x) for x in re.findall(r"\d+", text) if 0 < int(x) < n_events})
            return nums

    # -- Azure Responses ---------------------------------------------------
    def _azure_client(self, is_async: bool):
        from openai import AsyncAzureOpenAI, AzureOpenAI

        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
        if not api_key or not endpoint:
            raise SystemExit(
                "Azure Responses needs AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT "
                "(set them in benchmarks/memory_event_segmentation/.env)."
            )
        if is_async:
            return AsyncAzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)
        return AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)

    def _azure_call(self, stream: Stream) -> LLMResult:
        if self._client is None:
            self._client = self._azure_client(is_async=False)
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not deployment:
            raise SystemExit("Set AZURE_OPENAI_DEPLOYMENT (your model deployment name) in .env.")
        start = time.perf_counter()
        resp = self._client.responses.create(  # type: ignore[attr-defined]
            model=deployment,
            instructions=_PROMPT_SYSTEM,
            input=self._render(stream),
        )
        latency = time.perf_counter() - start
        text = getattr(resp, "output_text", "") or ""
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        return LLMResult(
            provider=self.provider,
            model_label=self.model_label,
            boundaries=self._parse(text, stream.n_events),
            latency_s=latency,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._cost(in_tok, out_tok),
            raw=text,
        )

    async def _azure_acall(self, stream: Stream) -> LLMResult:
        if self._aclient is None:
            self._aclient = self._azure_client(is_async=True)
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not deployment:
            raise SystemExit("Set AZURE_OPENAI_DEPLOYMENT (your model deployment name) in .env.")
        start = time.perf_counter()
        resp = await self._aclient.responses.create(  # type: ignore[attr-defined]
            model=deployment,
            instructions=_PROMPT_SYSTEM,
            input=self._render(stream),
        )
        latency = time.perf_counter() - start
        text = getattr(resp, "output_text", "") or ""
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        return LLMResult(
            provider=self.provider,
            model_label=self.model_label,
            boundaries=self._parse(text, stream.n_events),
            latency_s=latency,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._cost(in_tok, out_tok),
            raw=text,
        )

    # -- ollama (on-prem SLM) ---------------------------------------------
    def _ollama_call(self, stream: Stream) -> LLMResult:
        body = json.dumps(
            {
                "model": self.ollama_model,
                "prompt": _PROMPT_SYSTEM + "\n\n" + self._render(stream),
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            }
        ).encode()
        req = urllib.request.Request(
            self.ollama_host.rstrip("/") + "/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=1800) as r:  # noqa: S310 (local host)
            payload = json.loads(r.read().decode())
        latency = time.perf_counter() - start
        text = payload.get("response", "")
        in_tok = int(payload.get("prompt_eval_count", 0) or 0)
        out_tok = int(payload.get("eval_count", 0) or 0)
        return LLMResult(
            provider=self.provider,
            model_label=self.ollama_model,
            boundaries=self._parse(text, stream.n_events),
            latency_s=latency,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=0.0,  # on-prem: no per-token cost
            raw=text,
        )

    # -- public ------------------------------------------------------------
    def detect(self, stream: Stream) -> LLMResult:
        if self.provider == "ollama":
            return self._ollama_call(stream)
        return self._azure_call(stream)

    async def adetect(self, stream: Stream) -> LLMResult:
        if self.provider == "ollama":
            import asyncio

            return await asyncio.to_thread(self._ollama_call, stream)
        return await self._azure_acall(stream)
