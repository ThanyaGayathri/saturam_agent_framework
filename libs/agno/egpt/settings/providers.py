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

from typing import TYPE_CHECKING, Any, List, Optional, Union

from pydantic import Field

from egpt.settings.sources import BrandedSettings, bare_env_name, vendor_env
from egpt.utils.env import get_branded_env_with_bare_fallback

if TYPE_CHECKING:
    from egpt.providers.budget import BudgetLimits


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
    #
    # Path to a JSON policy document consumed by `ProviderConfig.from_dict`. None means "no
    # document": a deployment may equally build its `ProviderConfig` in code and hand it to
    # `ConfigDrivenModelProvider(...)` directly, which is what the tests do.
    policy_file: Optional[str] = None
    # Which verified JWT claim carries the tenant. There is no tenant concept in this tree
    # yet, so `egpt/os/utils.py` reads this claim when present and falls back to `user_id`;
    # the fallback is what actually runs today. Named here so a deployment whose IdP calls it
    # `org_id` or `tid` does not have to patch the framework.
    tenant_claim: str = "tenant_id"

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
    # raises without one, so Mode A supplies a placeholder when nothing is configured.
    mode_a_api_key: Optional[str] = None

    # The credential each backend documents for itself, declared here rather than read with a
    # raw `getenv` (which `tests/unit/test_env_read_guard.py` rejects for `egpt/providers/`).
    # Mode A passes `api_key` explicitly so this module is the only thing deciding it, and that
    # suppresses the backend's own fallback -- VLLM reads `VLLM_API_KEY` only when the field is
    # falsy, so the truthy "not-provided" placeholder made the documented setting dead, and a
    # token-protected in-VPC server started 401ing every run. `vendor_env` gives the vendor
    # spelling as the final fallback with no deprecation warning, since the SDK owns that name;
    # `EGPT_MODE_A_API_KEY` still outranks both as the Mode A-specific statement.
    mode_a_vllm_api_key: Optional[str] = Field(default=None, json_schema_extra=vendor_env("VLLM_API_KEY"))
    mode_a_ollama_api_key: Optional[str] = Field(default=None, json_schema_extra=vendor_env("OLLAMA_API_KEY"))

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
    #
    # Typed to accept a bare string as well as a list, and normalized by
    # `mode_a_allowed_hosts_list` below. pydantic-settings JSON-decodes *complex* fields, and
    # that decode happens in the settings source, before any field validator: with a plain
    # `List[str]`, the bare `EGPT_MODE_A_ALLOWED_HOSTS=llm.acme.com` that Mode A's own
    # remediation text tells the operator to set raised SettingsError from the source -- which
    # broke every read of ProviderSettings in the process rather than just this field, so
    # `default_mode` and all of Mode B became unreadable too.
    #
    # `Union[str, List[str]]` is not "complex" in the sense that triggers the source's JSON
    # decode, so the env string arrives intact; a programmatic caller passing a real list still
    # type-checks and still works. Read through the property, never this field directly.
    mode_a_allowed_hosts: Union[str, List[str], None] = None

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
    #
    # Which frontier backend to build when the caller pins nothing. These are registry keys
    # ("anthropic", "openai", "google", "aws-bedrock", "azure-openai"), not import paths:
    # naming one imports nothing, and `discovery.construct` turns the string into a class only
    # when a resolution actually happens.
    mode_b_provider: Optional[str] = None
    mode_b_model: Optional[str] = None

    # Optional gateway in front of the frontier. None means "talk to the frontier directly".
    # "portkey" is the tested path; "litellm" is code-only and raises at resolve time while the
    # package stays blocked (libs/agno/pyproject.toml ~line 249).
    mode_b_gateway: Optional[str] = None
    # EGPT-owned routing config for that gateway.
    #
    # `PORTKEY_*` is the vendor's supported convention, not a deprecated EGPT spelling. Use a
    # non-warning vendor fallback rather than `bare_env`, while keeping EGPT_/AGNO_ precedence
    # and resolving the values before Mode B performs its clear missing-credential checks.
    mode_b_gateway_api_key: Optional[str] = Field(default=None, json_schema_extra=vendor_env("PORTKEY_API_KEY"))
    mode_b_gateway_base_url: Optional[str] = None
    mode_b_gateway_virtual_key: Optional[str] = Field(default=None, json_schema_extra=vendor_env("PORTKEY_VIRTUAL_KEY"))

    # -- per-tenant budget (EGPT-3.6) --------------------------------------
    # A deployment-wide default budget, applied to any tenant whose policy names none. The
    # per-tenant value on `ModelPolicy.budget` always wins -- this is the floor for deployments
    # that want a blanket ceiling without writing a policy row per tenant.
    #
    # All three unset means no budget anywhere, which is the pre-EGPT-3.6 behaviour and costs
    # nothing: the enforcer returns before touching a counter.
    budget_max_tokens: Optional[int] = None
    budget_max_requests: Optional[int] = None
    budget_window: str = "hour"

    # Where counters live. Unset means the in-memory store, which counts per worker process --
    # `BudgetEnforcer` warns about that at construction because with N workers the effective
    # limit is N times the configured value. Set a redis:// URL for accurate multi-worker
    # enforcement (EGPT-3.6 unit 7).
    budget_redis_url: Optional[str] = None

    # -- derived views -----------------------------------------------------

    @property
    def default_budget(self) -> Optional["BudgetLimits"]:
        """The deployment-wide default budget, or ``None`` when nothing is configured.

        Returns ``None`` rather than a disabled ``BudgetLimits`` so the caller's ``is None`` check
        is the whole hot-path guard -- constructing an object that constrains nothing would make
        every no-budget deployment pay for the allocation on every resolve.
        """
        if self.budget_max_tokens is None and self.budget_max_requests is None:
            return None

        from egpt.providers.budget import BudgetLimits

        return BudgetLimits(
            max_tokens=self.budget_max_tokens,
            max_requests=self.budget_max_requests,
            window=self.budget_window,
        )

    @property
    def mode_a_allowed_hosts_list(self) -> List[str]:
        """``mode_a_allowed_hosts`` split into hostnames.

        Comma-separated is the documented form, matching what Mode A's remediation text tells
        the operator to write. A JSON array is still accepted, so a deployment that wrote one
        while this field was typed ``List[str]`` keeps working across the upgrade.
        """
        configured = self.mode_a_allowed_hosts
        if configured is None:
            return []
        if not isinstance(configured, str):
            return [str(item).strip() for item in configured if str(item).strip()]
        raw = configured.strip()
        if not raw:
            return []
        if raw.startswith("["):
            from json import JSONDecodeError, loads

            try:
                decoded = loads(raw)
            except JSONDecodeError:
                pass
            else:
                if isinstance(decoded, list):
                    return [str(item).strip() for item in decoded if str(item).strip()]
        return [part.strip() for part in raw.split(",") if part.strip()]


def get_provider_settings(**overrides: Any) -> ProviderSettings:
    """Build a fresh snapshot of the provider settings from the current environment.

    No module-level singleton and no cache, matching ``egpt.settings.get_settings``: a test
    that sets an endpoint after an earlier test warmed a cache must not read a stale value.
    """
    return ProviderSettings(**overrides)


def resolve_provider_setting(field_name: str) -> Optional[str]:
    """Live three-way read of a single ``ProviderSettings`` field, without building the model.

    The counterpart of ``egpt.settings.resolve``, and it exists for the same reason: reading one
    string on a per-request path must not construct and re-validate a whole pydantic model.
    ``tenant_claim`` is read on every factory-backed HTTP request, so that cost is real.

    Live, not cached, so the no-stale-reads property of ``get_provider_settings`` is preserved:
    a test that sets the variable after an earlier call still sees the new value.

    ``field_name`` must name a real field -- a typo raises ``KeyError`` rather than silently
    resolving to ``None``.
    """
    field = ProviderSettings.model_fields[field_name]
    return get_branded_env_with_bare_fallback(field_name.upper(), bare=bare_env_name(field))


__all__ = ["ProviderSettings", "get_provider_settings", "resolve_provider_setting"]
