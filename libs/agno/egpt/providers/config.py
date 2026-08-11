"""Declarative policy for dual-mode model resolution (EGPT-3.2).

``ModelResolveRequest`` says *what* is being asked. This module says *what the deployment
answers* -- which mode, which registry key, which model id, which adapter -- for a given
``(tenant_id, agent_id, role)``. Together they make "which model serves this request" a
configuration edit rather than a code change, which is Epic 3's whole point.

Three deliberate choices:

* **Plain dataclasses, not pydantic.** ``ProviderConfig`` is built in code and in tests far more
  often than it is parsed from a document, and ``ModelProviderConfigError`` (with its
  ``field_name``) is the error type the contract froze. A pydantic model would raise
  ``ValidationError`` instead, and every caller would have to translate. ``from_dict`` covers
  the parse-a-document case and reports the same error type.

* **Validation is eager, in ``__post_init__``.** A misconfigured deployment must fail when the
  config object is built -- at process start -- not on the first request that happens to route
  to the broken policy. EGPT-3.2's acceptance criterion is "invalid configuration is rejected
  with a clear error", and "clear" includes "before traffic".

* **No import of any provider class.** This module names registry keys as *strings*
  (``provider_key``) and nothing here imports ``egpt.registry`` or an integration, so
  ``tests/unit/registry/test_discovery_laziness.py`` stays green. Construction happens in
  ``config_provider.py``, inside the function that needs it.

Resolution order for a lookup, most specific first::

    (tenant_id, agent_id, role) -> (tenant_id, agent_id) -> (tenant_id,) -> default

so a deployment states the common case once and overrides only what differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional, Tuple

if TYPE_CHECKING:
    from egpt.providers.budget import BudgetLimits

from egpt.providers.model_provider import ModelProviderConfigError, ProviderMode

__all__ = [
    "ModelPolicy",
    "PolicyKey",
    "ProviderConfig",
]

# Registry keys a LOCAL policy is allowed to name. Not a hard whitelist -- an out-of-tree
# OpenAI-compatible backend registered through an entry point is legitimate -- but it is what
# ``ModelPolicy`` falls back to when a LOCAL policy states no ``provider_key``.
_DEFAULT_LOCAL_PROVIDER_KEY = "vllm"
_DEFAULT_FRONTIER_PROVIDER_KEY = "openai-responses"


def _parse_mode(value: Any, *, field_name: str) -> ProviderMode:
    """Coerce a config value to a ``ProviderMode``, or fail with the offending field named.

    Accepts a ``ProviderMode`` unchanged and a string case-insensitively, because a policy
    document written by hand will say ``"Local"`` at least once.
    """
    if isinstance(value, ProviderMode):
        return value
    if value is None:
        raise ModelProviderConfigError(
            "A model policy must declare a mode ('local' or 'frontier'); got none.",
            field_name=field_name,
        )
    if isinstance(value, str):
        try:
            return ProviderMode(value.strip().lower())
        except ValueError:
            pass
    known = ", ".join(repr(m.value) for m in ProviderMode)
    raise ModelProviderConfigError(
        f"Unknown mode {value!r}. Expected one of: {known}.",
        field_name=field_name,
    )


@dataclass(frozen=True)
class ModelPolicy:
    """What one identity gets: a mode, a backend, a model id, and optional adapter.

    Frozen for the same reason ``ModelResolveRequest`` is: a policy is read concurrently by
    every in-flight request and must never be mutated under one of them.

    ``provider_key`` is a *registry* key (``"vllm"``, ``"openai-responses"``, ``"anthropic"``),
    never an imported class. Left unset it defaults per mode, so the minimal useful policy is
    ``ModelPolicy(mode="local", model_id="qwen3-8b")``.
    """

    mode: ProviderMode
    model_id: Optional[str] = None
    provider_key: Optional[str] = None
    # A LoRA adapter name. Mode A (EGPT-3.3) renders it; a FRONTIER policy naming one is a
    # configuration error, not a silently-ignored field -- see __post_init__.
    adapter: Optional[str] = None
    # Extra kwargs forwarded verbatim to ``discovery.construct`` (base_url, temperature, ...).
    # Kept opaque so a backend can take config this module has never heard of.
    config: Mapping[str, Any] = field(default_factory=dict)
    # Human-readable note carried onto ``ModelResolveResult.reason``.
    reason: Optional[str] = None
    # Per-tenant spend and rate ceiling (EGPT-3.6). None means "no budget", which is the
    # pre-EGPT-3.6 behaviour and costs nothing on the hot path: the enforcer returns before any
    # counter read. Carried on the policy rather than in a separate document because a budget is
    # a property of *what this identity gets*, exactly like the mode and the backend -- keeping
    # it here means one lookup answers both "which model" and "how much of it".
    budget: Optional["BudgetLimits"] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _parse_mode(self.mode, field_name="mode"))
        if not isinstance(self.config, Mapping):
            # Checked before dict(): a string or list "config" in a policy document would
            # otherwise surface as a bare builtin ValueError ("dictionary update sequence
            # element #0 ...") with no field name and no policy attribution -- invisible to
            # every `except ModelProviderConfigError` handler.
            raise ModelProviderConfigError(
                f"config must be a mapping of constructor kwargs, got {type(self.config).__name__}.",
                field_name="config",
            )
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))

        if self.model_id is not None and not str(self.model_id).strip():
            raise ModelProviderConfigError(
                "model_id must be a non-empty string when set.",
                field_name="model_id",
            )
        if self.provider_key is not None and not str(self.provider_key).strip():
            raise ModelProviderConfigError(
                "provider_key must be a non-empty string when set.",
                field_name="provider_key",
            )
        if self.adapter is not None and self.mode is not ProviderMode.LOCAL:
            # Not a warning. A LoRA adapter is a statement about *which weights answer*; a
            # frontier host cannot honour it, so accepting the config would serve a different
            # model than the operator asked for and say nothing about it.
            raise ModelProviderConfigError(
                f"adapter {self.adapter!r} is only meaningful in mode 'local'; "
                f"policy declares mode {self.mode.value!r}.",
                field_name="adapter",
            )

    @property
    def effective_provider_key(self) -> str:
        """The registry key to construct through, defaulted per mode when unset."""
        if self.provider_key:
            return self.provider_key
        return _DEFAULT_LOCAL_PROVIDER_KEY if self.mode is ProviderMode.LOCAL else _DEFAULT_FRONTIER_PROVIDER_KEY

    def merged_config(self, **extra: Any) -> Dict[str, Any]:
        """This policy's construct kwargs plus ``extra``. Caller-supplied values win."""
        merged = dict(self.config)
        merged.update({k: v for k, v in extra.items() if v is not None})
        return merged

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelPolicy":
        """Build a policy from a plain mapping (a parsed YAML/JSON policy document).

        Unknown keys are rejected rather than ignored: a typo'd ``"modle"`` that silently
        produced a default-mode policy is exactly the misconfiguration this ticket exists to
        surface.
        """
        if not isinstance(data, Mapping):
            raise ModelProviderConfigError(
                f"A model policy must be a mapping, got {type(data).__name__}.",
                field_name="policy",
            )
        known = {"mode", "model_id", "provider_key", "adapter", "config", "reason", "budget"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ModelProviderConfigError(
                f"Unknown policy field(s): {', '.join(unknown)}. Known fields: {', '.join(sorted(known))}.",
                field_name=unknown[0],
            )
        if "mode" not in data:
            raise ModelProviderConfigError(
                "A model policy must declare a mode ('local' or 'frontier'); got none.",
                field_name="mode",
            )
        return cls(
            mode=_parse_mode(data["mode"], field_name="mode"),
            model_id=data.get("model_id"),
            provider_key=data.get("provider_key"),
            adapter=data.get("adapter"),
            config=data.get("config") or {},
            reason=data.get("reason"),
            budget=_parse_budget(data.get("budget")),
        )


def _parse_budget(value: Any) -> Optional["BudgetLimits"]:
    """Build ``BudgetLimits`` from a policy document's ``budget`` block, or ``None``.

    Wraps ``BudgetLimits``'s own ``ValueError`` into a ``ModelProviderConfigError`` carrying the
    offending field name. Without that, a typo'd window in a JSON policy file surfaced as a bare
    builtin error with no attribution -- invisible to every ``except ModelProviderConfigError``
    handler, which is the same gap the ``config`` mapping check above exists to close.

    Imported inside the function: ``providers/budget.py`` is pure mechanism and importing it at
    module scope would make every ``import egpt.providers.config`` pay for it, including the
    deployments that never configure a budget.
    """
    if value is None:
        return None

    from egpt.providers.budget import BudgetLimits

    if not isinstance(value, Mapping):
        raise ModelProviderConfigError(
            f"budget must be a mapping of limits, got {type(value).__name__}.",
            field_name="budget",
        )

    known = {"max_tokens", "max_requests", "window"}
    unknown = sorted(set(value) - known)
    if unknown:
        # Same posture as the policy-level check: an unrecognised key is a typo that would
        # otherwise be silently dropped, leaving an operator believing a limit is in force.
        raise ModelProviderConfigError(
            f"Unknown budget field(s): {', '.join(unknown)}. Known fields: {', '.join(sorted(known))}.",
            field_name=unknown[0],
        )

    try:
        return BudgetLimits(
            max_tokens=value.get("max_tokens"),
            max_requests=value.get("max_requests"),
            window=value.get("window", "hour"),
        )
    except ValueError as e:
        field = "window" if "window" in str(e) else ("max_tokens" if "max_tokens" in str(e) else "max_requests")
        raise ModelProviderConfigError(str(e), field_name=field) from None


# (tenant_id, agent_id, role). ``None`` in a slot means "any". Kept as a plain tuple rather
# than a dataclass so it is trivially hashable and reads as a dict key at every call site.
PolicyKey = Tuple[Optional[str], Optional[str], Optional[str]]


@dataclass(frozen=True)
class ProviderConfig:
    """The whole deployment's model-resolution policy, in one object.

    One ``ProviderConfig`` plus two different identities is the headline of EGPT-3.2: the same
    code path resolves to a local SLM for one and a frontier model for the other, with no code
    change. ``lookup`` is that mapping and it is pure -- no I/O, no imports, no SDK.

    ``policies`` is keyed by ``PolicyKey``; ``default`` answers anything unmatched. A config
    with neither is legal and means "no mode configured": ``lookup`` returns ``None`` and the
    seam stays the no-op EGPT-3.1 designed.
    """

    default: Optional[ModelPolicy] = None
    policies: Mapping[PolicyKey, ModelPolicy] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: Dict[PolicyKey, ModelPolicy] = {}
        for raw_key, policy in dict(self.policies).items():
            key = self._normalize_key(raw_key)
            if not isinstance(policy, ModelPolicy):
                raise ModelProviderConfigError(
                    f"Policy for {key!r} must be a ModelPolicy, got {type(policy).__name__}.",
                    field_name="policies",
                )
            normalized[key] = policy
        object.__setattr__(self, "policies", MappingProxyType(normalized))

        if self.default is not None and not isinstance(self.default, ModelPolicy):
            raise ModelProviderConfigError(
                f"default must be a ModelPolicy, got {type(self.default).__name__}.",
                field_name="default",
            )

        if self.default is not None and (None, None, None) in normalized:
            # `lookup` consults candidate keys before `default`, and (None, None, None) is a
            # candidate for every identity -- so this pair means `default` can never win, and
            # editing it silently changes nothing. Two defaults, one live, no error is exactly
            # the misconfiguration shape this module rejects everywhere else.
            raise ModelProviderConfigError(
                "A policy keyed (None, None, None) matches every identity and shadows "
                "'default' entirely. Keep one: either the identity-less policy or 'default'.",
                field_name="policies",
            )

    @staticmethod
    def _normalize_key(raw_key: Any) -> PolicyKey:
        """Pad a 1- or 2-tuple (or a bare tenant string) out to the full 3-slot key."""
        if isinstance(raw_key, str) or raw_key is None:
            parts: Tuple[Any, ...] = (raw_key,)
        elif isinstance(raw_key, tuple):
            parts = raw_key
        else:
            raise ModelProviderConfigError(
                f"Policy key must be a string or a tuple of up to 3 strings, got {type(raw_key).__name__}.",
                field_name="policies",
            )
        if len(parts) > 3:
            raise ModelProviderConfigError(
                f"Policy key {raw_key!r} has {len(parts)} parts; expected at most 3 (tenant_id, agent_id, role).",
                field_name="policies",
            )
        padded = tuple(parts) + (None,) * (3 - len(parts))
        for part in padded:
            if part is not None and not isinstance(part, str):
                raise ModelProviderConfigError(
                    f"Policy key {raw_key!r} has a non-string part {part!r}.",
                    field_name="policies",
                )
        return (padded[0], padded[1], padded[2])

    def candidate_keys(
        self,
        tenant_id: Optional[str],
        agent_id: Optional[str],
        role: Optional[str],
    ) -> Tuple[PolicyKey, ...]:
        """Every key that could serve this identity, most specific first.

        Exposed rather than kept private because a diagnostic ("why did this request get the
        frontier policy?") is answered by showing this list next to ``policies``.
        """
        return (
            (tenant_id, agent_id, role),
            (tenant_id, agent_id, None),
            (tenant_id, None, role),
            (tenant_id, None, None),
            (None, agent_id, role),
            (None, agent_id, None),
            (None, None, role),
            (None, None, None),
        )

    def lookup(
        self,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Optional[ModelPolicy]:
        """The most specific policy for this identity, or ``default``, or ``None``.

        Pure and cheap: at most eight dict lookups, no I/O, no import.
        """
        for key in self.candidate_keys(tenant_id, agent_id, role):
            policy = self.policies.get(key)
            if policy is not None:
                return policy
        return self.default

    def with_policy(self, key: Any, policy: ModelPolicy) -> "ProviderConfig":
        """A copy with one policy added or replaced. Pure construction -- nothing to await."""
        updated = dict(self.policies)
        updated[self._normalize_key(key)] = policy
        return replace(self, policies=updated)

    def modes(self) -> Tuple[ProviderMode, ...]:
        """Every mode this config can produce, sorted and de-duplicated.

        ``ConfigDrivenModelProvider.capabilities()`` reports these, so a caller can ask "can
        this deployment serve locally?" without resolving anything.
        """
        seen: Iterable[ModelPolicy] = list(self.policies.values()) + ([self.default] if self.default else [])
        return tuple(sorted({p.mode for p in seen}, key=lambda m: m.value))

    def provider_keys(self) -> Tuple[str, ...]:
        """Every registry key this config may construct through. Strings only -- imports nothing."""
        seen = list(self.policies.values()) + ([self.default] if self.default else [])
        return tuple(sorted({p.effective_provider_key for p in seen}))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProviderConfig":
        """Build a config from a parsed policy document.

        Expected shape::

            {
              "default": {"mode": "frontier", "model_id": "gpt-5.5"},
              "policies": [
                {"tenant_id": "acme", "mode": "local", "model_id": "qwen3-8b"},
                {"tenant_id": "acme", "agent_id": "support", "role": "parser",
                 "mode": "local", "model_id": "qwen3-1.7b", "adapter": "support-v3"},
              ],
            }

        ``policies`` is a *list* of records rather than a mapping because the identity slots
        belong inside the record: a JSON document cannot key a mapping by a tuple, and encoding
        one as ``"acme|support|parser"`` invents a delimiter that tenant ids can contain.
        """
        if not isinstance(data, Mapping):
            raise ModelProviderConfigError(
                f"Provider config must be a mapping, got {type(data).__name__}.",
                field_name="config",
            )
        unknown = sorted(set(data) - {"default", "policies"})
        if unknown:
            raise ModelProviderConfigError(
                f"Unknown config field(s): {', '.join(unknown)}. Known fields: default, policies.",
                field_name=unknown[0],
            )

        default_raw = data.get("default")
        default = ModelPolicy.from_dict(default_raw) if default_raw is not None else None

        raw_policies = data.get("policies") or []
        if isinstance(raw_policies, Mapping):
            raise ModelProviderConfigError(
                "'policies' must be a list of policy records, each carrying its own "
                "tenant_id/agent_id/role, not a mapping.",
                field_name="policies",
            )
        if not isinstance(raw_policies, (list, tuple)):
            raise ModelProviderConfigError(
                f"'policies' must be a list, got {type(raw_policies).__name__}.",
                field_name="policies",
            )

        policies: Dict[PolicyKey, ModelPolicy] = {}
        for record in raw_policies:
            if not isinstance(record, Mapping):
                raise ModelProviderConfigError(
                    f"Each policy record must be a mapping, got {type(record).__name__}.",
                    field_name="policies",
                )
            key: PolicyKey = (
                record.get("tenant_id"),
                record.get("agent_id"),
                record.get("role"),
            )
            normalized = cls._normalize_key(key)
            if normalized == (None, None, None):
                # Such a record is a second 'default' in disguise: it would win every lookup
                # before the 'default' block is consulted, so edits to 'default' would change
                # nothing, silently. Reject it whether or not 'default' is present -- the
                # 'default' block is the one spelling of "matches everyone" this document has.
                raise ModelProviderConfigError(
                    "A policy record with no tenant_id/agent_id/role matches every identity. "
                    "Put deployment-wide policy in the 'default' block instead.",
                    field_name="policies",
                )
            if normalized in policies:
                # Last-wins would route a tenant to the wrong mode with no error -- exactly the
                # silent misconfiguration this module rejects a typo'd field name for.
                raise ModelProviderConfigError(
                    f"Duplicate policy for identity {normalized!r}. Each "
                    "(tenant_id, agent_id, role) may appear at most once.",
                    field_name="policies",
                )
            body = {k: v for k, v in record.items() if k not in ("tenant_id", "agent_id", "role")}
            policies[normalized] = ModelPolicy.from_dict(body)

        return cls(default=default, policies=policies)
