"""LLM backend for the harness.

Three concerns live here:

1. ``AzureOpenAIResponses`` — a thin subclass of egpt's ``OpenAIResponses`` that
   swaps in the Azure client. We deliberately keep the **Responses API** (house
   rule: Responses, not Chat) while pointing at the Azure deployment. Because
   egpt calls ``client.responses.create(...)`` and the openai SDK's
   ``AzureOpenAI`` client exposes ``.responses``, the swap is transparent.

2. ``LLMClient`` — a record/replay cache + token/latency/cost accounting wrapper.
   Real Azure calls are cached under ``results/llm_cache/`` so reruns are free and
   deterministic, and so the whole harness can run offline once responses exist.

3. Cost model — a configurable price table (assumptions documented in RESEARCH.md).

Modes (env ``HARNESS_LLM_MODE``):
  - ``auto``   (default): replay from cache if present, else call live and record.
  - ``replay``: cache only; raise if a prompt is missing (fully offline, CI-safe).
  - ``live``  : always call the model (still records).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from egpt.models.message import Message
from egpt.models.openai import OpenAIResponses

# --------------------------------------------------------------------------- #
# Cost model (per 1M tokens, USD). Placeholder rates — see RESEARCH.md.
# Override at runtime with HARNESS_PRICE_IN / HARNESS_PRICE_OUT (USD per 1M).
# --------------------------------------------------------------------------- #
_DEFAULT_PRICE_IN = 1.25
_DEFAULT_PRICE_OUT = 10.0


def _price_per_1m() -> Tuple[float, float]:
    price_in = float(os.getenv("HARNESS_PRICE_IN", _DEFAULT_PRICE_IN))
    price_out = float(os.getenv("HARNESS_PRICE_OUT", _DEFAULT_PRICE_OUT))
    return price_in, price_out


def load_env(dotenv_path: Optional[Path] = None) -> None:
    """Minimal .env loader (no third-party dependency).

    Populates os.environ from ``benchmarks/memory_consolidation/.env`` for keys
    that are not already set. Existing environment always wins.
    """
    if dotenv_path is None:
        dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    if not dotenv_path.exists():
        return
    for raw in dotenv_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class AzureOpenAIResponses(OpenAIResponses):
    """egpt OpenAIResponses pointed at an Azure deployment via the Responses API."""

    def __init__(
        self,
        deployment: str,
        azure_endpoint: str,
        api_version: str,
        api_key: str,
        **kwargs: Any,
    ) -> None:
        # For Azure, the Responses API 'model' argument is the *deployment name*.
        super().__init__(id=deployment, api_key=api_key, **kwargs)
        self._azure_endpoint = azure_endpoint
        self._api_version = api_version

    def get_client(self):  # type: ignore[override]
        if self.client is not None and not self.client.is_closed():
            return self.client
        from openai import AzureOpenAI as _AzureOpenAI

        self.client = _AzureOpenAI(
            api_key=self.api_key,
            api_version=self._api_version,
            azure_endpoint=self._azure_endpoint,
        )
        return self.client

    def get_async_client(self):  # type: ignore[override]
        if self.async_client is not None and not self.async_client.is_closed():
            return self.async_client
        from openai import AsyncAzureOpenAI as _AsyncAzureOpenAI

        self.async_client = _AsyncAzureOpenAI(
            api_key=self.api_key,
            api_version=self._api_version,
            azure_endpoint=self._azure_endpoint,
        )
        return self.async_client


def build_model(extractor: bool = False) -> OpenAIResponses:
    """Construct the configured Responses model from environment.

    ``extractor=True`` uses AZURE_OPENAI_EXTRACTOR_DEPLOYMENT if set (emulating a
    cheaper on-prem SLM extractor), else falls back to the main deployment.
    """
    load_env()
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if extractor:
        deployment = os.getenv("AZURE_OPENAI_EXTRACTOR_DEPLOYMENT", deployment or "")
    if not (api_key and endpoint and deployment):
        raise RuntimeError(
            "Azure credentials missing. Set AZURE_OPENAI_API_KEY, "
            "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT in "
            "benchmarks/memory_consolidation/.env (see .env.example). "
            "Or run in replay mode (HARNESS_LLM_MODE=replay) against a warm cache."
        )
    return AzureOpenAIResponses(
        deployment=deployment,
        azure_endpoint=endpoint,
        api_version=api_version,
        api_key=api_key,
    )


@dataclass
class _CacheEntry:
    content: str
    input_tokens: int
    output_tokens: int
    latency_s: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_s": self.latency_s,
        }


class LLMClient:
    """Record/replay wrapper around a Responses model with cost accounting.

    Public surface has both sync and async variants (house rule):
      - ``complete`` / ``acomplete``
      - ``complete_json`` / ``acomplete_json``
    """

    def __init__(
        self,
        model: Optional[OpenAIResponses] = None,
        cache_dir: Optional[Path] = None,
        mode: Optional[str] = None,
        model_label: str = "primary",
        extractor: bool = False,
    ) -> None:
        self.mode = (mode or os.getenv("HARNESS_LLM_MODE", "auto")).lower()
        self.model_label = model_label
        self._extractor = extractor
        self._model = model
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parent.parent / "results" / "llm_cache"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- lazy model construction so replay-only runs never need credentials ---
    @property
    def model(self) -> OpenAIResponses:
        if self._model is None:
            self._model = build_model(extractor=self._extractor)
        return self._model

    def _model_id(self) -> str:
        # Stable label for the cache key; avoids leaking deployment name changes
        # from busting an otherwise identical prompt/response.
        return os.getenv("HARNESS_MODEL_TAG", "gpt-5.4")

    def _key(self, system: str, user: str, json_mode: bool) -> str:
        payload = json.dumps(
            {
                "model": self._model_id(),
                "label": self.model_label,
                "system": system,
                "user": user,
                "json_mode": json_mode,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _load(self, key: str) -> Optional[_CacheEntry]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return _CacheEntry(
            content=data["content"],
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            latency_s=data["latency_s"],
        )

    def _store(self, key: str, entry: _CacheEntry) -> None:
        self._cache_path(key).write_text(json.dumps(entry.to_dict(), indent=2))

    def _messages(self, system: str, user: str) -> List[Message]:
        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]

    def _account(self, entry: _CacheEntry, usage: Any) -> None:
        if usage is None:
            return
        price_in, price_out = _price_per_1m()
        cost = (entry.input_tokens * price_in + entry.output_tokens * price_out) / 1_000_000
        usage.add(
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            latency_s=entry.latency_s,
            usd_cost=cost,
        )

    # ------------------------------------------------------------------ sync
    def complete(self, system: str, user: str, usage: Any, json_mode: bool = False) -> str:
        key = self._key(system, user, json_mode)
        entry = self._load(key)
        if entry is None:
            if self.mode == "replay":
                raise RuntimeError(
                    f"replay miss for key {key[:12]} (label={self.model_label}); "
                    "run once with HARNESS_LLM_MODE=auto to warm the cache."
                )
            start = time.perf_counter()
            response = self.model.response(messages=self._messages(system, user))
            latency = time.perf_counter() - start
            u = response.response_usage
            entry = _CacheEntry(
                content=response.content or "",
                input_tokens=getattr(u, "input_tokens", 0) or 0,
                output_tokens=getattr(u, "output_tokens", 0) or 0,
                latency_s=latency,
            )
            self._store(key, entry)
        self._account(entry, usage)
        return entry.content

    def complete_json(self, system: str, user: str, usage: Any) -> Any:
        text = self.complete(system, user, usage, json_mode=True)
        return _parse_json(text)

    # ----------------------------------------------------------------- async
    async def acomplete(self, system: str, user: str, usage: Any, json_mode: bool = False) -> str:
        key = self._key(system, user, json_mode)
        entry = self._load(key)
        if entry is None:
            if self.mode == "replay":
                raise RuntimeError(
                    f"replay miss for key {key[:12]} (label={self.model_label}); "
                    "run once with HARNESS_LLM_MODE=auto to warm the cache."
                )
            start = time.perf_counter()
            response = await self.model.aresponse(messages=self._messages(system, user))
            latency = time.perf_counter() - start
            u = response.response_usage
            entry = _CacheEntry(
                content=response.content or "",
                input_tokens=getattr(u, "input_tokens", 0) or 0,
                output_tokens=getattr(u, "output_tokens", 0) or 0,
                latency_s=latency,
            )
            self._store(key, entry)
        self._account(entry, usage)
        return entry.content

    async def acomplete_json(self, system: str, user: str, usage: Any) -> Any:
        text = await self.acomplete(system, user, usage, json_mode=True)
        return _parse_json(text)


def _parse_json(text: str) -> Any:
    """Tolerant JSON extraction from an LLM response (handles ```json fences)."""
    text = text.strip()
    if text.startswith("```"):
        # strip a fenced block
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last resort: find the outermost JSON array/object
        for opener, closer in (("[", "]"), ("{", "}")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise
