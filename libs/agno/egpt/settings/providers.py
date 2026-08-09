"""Per-domain settings for the model provider seam (Epic 3).

Follows the per-domain-file convention: the central ``EgptSettings`` in
``egpt/settings/__init__.py`` owns the framework's own serving/auth surface, and each lane adds
its own module here rather than appending to that shared class body.

Every environment read for Epic 3 goes through this module. ``tests/unit/test_env_read_guard.py``
is a ratchet checked in both directions -- a raw ``getenv`` under ``egpt/providers/`` fails it,
and so does an allowlist entry with no matching access -- so providers must read settings from
here rather than reaching for the environment themselves.

Field blocks below are partitioned by ticket. EGPT-3.2, 3.3 and 3.4 are built in parallel
against this file; each adds fields only inside its own block so the three diffs stay
line-disjoint. A block that needs ``pydantic.Field`` or ``bare_env`` adds that import itself --
they are omitted here rather than pre-imported, because an unused import fails ruff and a
placeholder reference to silence it is worse than three branches touching one import line.
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import Field

from egpt.settings.sources import BrandedSettings


class ProviderSettings(BrandedSettings):
    """Environment configuration for dual-mode model resolution.

    Read via ``EGPT_<FIELD>``, falling back to ``AGNO_<FIELD>`` and then to an opt-in bare name
    where a field declares one -- the same three-way chain every other EGPT settings model uses.
    """

    # -- shared (EGPT-3.1) -------------------------------------------------
    # Which mode to use when nothing more specific applies. None means "no mode configured",
    # which is the seam's no-op path: agents resolve their model exactly as before Epic 3.
    default_mode: Optional[str] = None

    # -- config-driven resolution (EGPT-3.2) -------------------------------
    # Owned by #64. Path to the per-tenant policy document, and the fallback identity claim.
    # (Add fields here.)

    # -- Mode A: self-hosted SLM (EGPT-3.3) --------------------------------
    # Owned by #65. The in-VPC endpoint and adapter configuration.
    #
    # `mode_a_base_url` has NO default, deliberately. `integrations/models/ollama/chat.py`
    # falls back to `https://ollama.com` and `vllm.py` to `http://localhost:8000/v1/` when
    # unset, so a Mode A binding that lets either default through has silently left the VPC.
    # `LocalModelProvider` raises `ModelProviderConfigError` on None rather than constructing
    # anything, and always passes the configured endpoint explicitly so neither SDK default
    # can be reached.
    mode_a_base_url: Optional[str] = None

    # Which registry key serves Mode A. "vllm", "ollama" or "openai-like" -- all built by
    # name through `discovery.construct`, so naming one here imports nothing.
    mode_a_backend: str = "vllm"

    # The model id served at that endpoint. A self-hosted deployment names its own weights,
    # so there is no sensible default to guess.
    mode_a_model_id: Optional[str] = None

    # Credential for the endpoint. In-VPC vLLM/Ollama servers are commonly unauthenticated;
    # `OpenAILike` defaults `api_key` to "not-provided" for exactly that case, and VLLM
    # raises without one, so Mode A supplies a placeholder rather than reading VLLM_API_KEY.
    mode_a_api_key: Optional[str] = None

    # How a LoRA adapter is rendered onto the request. "model-id" follows vLLM's multi-LoRA
    # convention, where each adapter is served as a first-class model id -- the default,
    # because it is what a stock `vllm serve --lora-modules` deployment expects.
    # "extra-body" instead threads it through the OpenAI `extra_body` passthrough, for
    # servers that take the adapter as a side-channel parameter.
    mode_a_adapter_mode: str = "model-id"

    # The `extra_body` key an adapter is written to under `mode_a_adapter_mode="extra-body"`.
    mode_a_adapter_field: str = "lora_request"

    # Extra hostnames or domain suffixes that count as in-VPC. Mode A treats loopback, private
    # IPs, dotless names and the usual internal suffixes (.internal, .svc.cluster.local, ...)
    # as inside the network; a deployment whose internal domain is a public-looking name needs
    # to name it here, or its own endpoint would be refused as egress.
    mode_a_allowed_hosts: List[str] = Field(default_factory=list)

    # Context window of the served model, when the deployment knows it. Advisory only:
    # EGPT-3.5's "input too large" rule reads it, and None means "do not route on it".
    mode_a_context_window: Optional[int] = None

    # -- Mode B: frontier provider (EGPT-3.4) ------------------------------
    # Owned by #66. Frontier selection plus optional gateway routing.
    #
    # Note for the implementer: credentials for the frontier SDKs (OPENAI_API_KEY, etc.) are
    # deliberately NOT fields here. Their names are owned by the provider SDK convention, and
    # `egpt/settings/__init__.py` documents why the framework does not re-namespace them. Only
    # gateway endpoints and EGPT-owned routing config belong in this block.
    # (Add fields here.)


def get_provider_settings(**overrides: Any) -> ProviderSettings:
    """Build a fresh snapshot of the provider settings from the current environment.

    No module-level singleton and no cache, matching ``egpt.settings.get_settings``: a test
    that sets an endpoint after an earlier test warmed a cache must not read a stale value.
    """
    return ProviderSettings(**overrides)


__all__ = ["ProviderSettings", "get_provider_settings"]
