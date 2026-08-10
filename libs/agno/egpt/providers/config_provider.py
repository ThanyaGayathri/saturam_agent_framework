"""Config-driven model resolution (EGPT-3.2).

``ConfigDrivenModelProvider`` maps ``(tenant_id, agent_id, role)`` to a ``ModelPolicy`` and
builds the resulting ``Model`` through ``egpt.registry.discovery.construct``. It is the piece
that makes mode selection a configuration decision: one ``ProviderConfig``, two identities, two
different backends, no code change.

What this module deliberately does *not* do:

* **It does not import ``providers/mode_a.py`` or ``providers/mode_b.py``.** Those bindings
  (EGPT-3.3 / 3.4) are built in parallel against the same frozen contract. This provider needs
  only a registry *key* to reach a backend, and a key is a string. When the mode bindings land
  they compose with this one by being named in a policy's ``provider_key`` or by wrapping it --
  neither requires an edit here.

* **It does not import any provider class at module scope.** ``discovery`` is imported inside
  ``_build``, so importing this module touches no registry table and no SDK.
  ``tests/unit/registry/test_discovery_laziness.py`` runs in a clean subprocess and pins that.

* **It does not read the environment.** Every env read for Epic 3 lives in
  ``egpt/settings/providers.py``; ``from_settings`` calls into it rather than reaching for
  ``getenv``, which ``tests/unit/test_env_read_guard.py`` checks in both directions.

Precedence, highest first:

1. ``request.requested`` -- an explicit ``Agent(model=...)``. The contract requires returning
   it unchanged when no policy overrides it, because user intent outranks configuration.
2. ``request.mode`` -- an explicit caller override, authoritative over the configured mode.
3. The matched ``ModelPolicy``.
4. Nothing matched and no default -> ``ModelProviderConfigError``.
"""

from __future__ import annotations

from typing import Any, Optional

from egpt.models.base import Model
from egpt.providers.config import ModelPolicy, ProviderConfig, _parse_mode
from egpt.providers.model_provider import (
    BaseModelProvider,
    ModelCapabilities,
    ModelProviderConfigError,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
)

__all__ = ["ConfigDrivenModelProvider"]


class ConfigDrivenModelProvider(BaseModelProvider):
    """Resolve a request against a declarative ``ProviderConfig``.

    Holds no mutable state beyond the config it was constructed with, so it is safe to share
    across threads -- which it must be: ``Agent`` runs tool calls on a ``ThreadPoolExecutor``.
    """

    def __init__(self, config: ProviderConfig, *, name: str = "config-driven") -> None:
        if not isinstance(config, ProviderConfig):
            raise ModelProviderConfigError(
                f"config must be a ProviderConfig, got {type(config).__name__}.",
                field_name="config",
            )
        self.config = config
        self.name = name

    # -- resolution ---------------------------------------------------------

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Pick a policy for this request and build its model.

        ``BaseModelProvider.resolve`` delegates here, so implementing one method gives both.
        """
        # An explicit Agent(model=...) wins unless a policy pins a different model id. Looked
        # up through `_pinning_policy` -- never `policy_for` -- because the docstring ranks
        # `requested` above `mode`: a caller who pinned both a model and a mode must get a
        # model back, not an error about the mode they no longer need served. That holds on
        # BOTH branches below: re-consulting `policy_for` here would re-raise the very
        # unsatisfiable-mode error `_pinning_policy` exists to swallow, turning a pinned run
        # that should succeed into a hard failure whenever the matched policy carries a
        # model_id (the previously-untested half of the pinning contract).
        if request.requested is not None:
            policy = self._pinning_policy(request)
            if policy is None or policy.model_id is None:
                return ModelResolveResult(
                    model=request.requested,
                    mode=self._mode_for_pinned(request, policy),
                    provider_key=self._registry_key_for(request.requested),
                    reason="explicit model on the request",
                )
            # A policy pinning its own model_id overrides the caller's model; fall through and
            # build it from the policy already in hand.
        else:
            policy = self.policy_for(request)

        if policy is None:
            raise ModelProviderConfigError(
                "No model policy matched this request and the config declares no default. "
                f"Identity: tenant_id={request.tenant_id!r}, agent_id={request.agent_id!r}, "
                f"role={request.role!r}.",
                field_name="default",
            )

        if policy.model_id is None:
            # A policy that names a mode but no model cannot resolve anything. Caught here as
            # well as in validation because `from_dict` is not the only way to build one.
            raise ModelProviderConfigError(
                f"Policy for mode {policy.mode.value!r} declares no model_id, so nothing can be resolved.",
                field_name="model_id",
            )

        provider_key = policy.effective_provider_key
        # Applied-or-None, decided once and shared with _build: reporting an adapter the build
        # did not inject would tell any per-adapter consumer that LoRA weights answered when a
        # stock model did -- the exact mismatch the frontier-adapter guard exists to prevent,
        # one field over.
        adapter = request.adapter or policy.adapter
        applied_adapter = adapter if (adapter and policy.mode is ProviderMode.LOCAL) else None
        model = self._build(policy, adapter=applied_adapter)
        return ModelResolveResult(
            model=model,
            mode=policy.mode,
            provider_key=provider_key,
            adapter=applied_adapter,
            reason=policy.reason,
        )

    def policy_for(self, request: ModelResolveRequest) -> Optional[ModelPolicy]:
        """The policy this request resolves against, honouring an explicit ``request.mode``.

        Pure: no I/O, no import. Split out from ``resolve_detailed`` so a diagnostic can ask
        "which policy would this request get?" without constructing a model or needing an SDK.
        """
        policy = self.config.lookup(
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            role=request.role,
        )
        if request.mode is None or policy is None or policy.mode is request.mode:
            return policy

        # The caller overrode the mode, so the matched policy's backend and model id belong to
        # the wrong family. Fall back to the config's default when it already speaks the
        # requested mode; otherwise the override cannot be honoured and saying so beats
        # quietly serving the other mode.
        default = self.config.default
        if default is not None and default.mode is request.mode:
            return default
        raise ModelProviderConfigError(
            f"Request asked for mode {request.mode.value!r} but no configured policy serves it "
            f"for tenant_id={request.tenant_id!r}, agent_id={request.agent_id!r}, role={request.role!r}.",
            field_name="mode",
        )

    def _pinning_policy(self, request: ModelResolveRequest) -> Optional[ModelPolicy]:
        """The policy that could override a pinned model, ignoring an unsatisfiable mode.

        ``policy_for`` raises when the caller's ``mode`` override cannot be served. That is the
        right answer when configuration is choosing the model, but not when the caller already
        pinned one: ``requested`` outranks ``mode``, so a failure to honour the override must
        not deny the caller the model they supplied.
        """
        try:
            return self.policy_for(request)
        except ModelProviderConfigError:
            return self.config.lookup(
                tenant_id=request.tenant_id,
                agent_id=request.agent_id,
                role=request.role,
            )

    def _mode_for_pinned(self, request: ModelResolveRequest, policy: Optional[ModelPolicy]) -> ProviderMode:
        """Which mode to report for a caller-pinned model.

        ``ModelResolveResult.mode`` is non-optional in the frozen contract, so something must be
        reported -- but it must not be *invented*. Reporting a default FRONTIER for a pinned
        in-VPC model would tell EGPT-3.5's escalation log, and anything that bills or audits on
        this field, that a request left the VPC when it did not.

        In order: the caller's explicit override (rank 2 in the module precedence, above the
        matched policy at rank 3 -- a caller who said FRONTIER and pinned a frontier model must
        not be logged as LOCAL, which would under-report egress to EGPT-3.5's escalation log
        and anything billing on this field), then the matched policy's mode, then the only mode
        this config can produce (unambiguous), and finally FRONTIER -- reached only when
        nothing at all is configured and nobody stated a mode, which is the EGPT-3.1 no-op path.
        """
        if request.mode is not None:
            return request.mode
        if policy is not None:
            return policy.mode
        modes = self.config.modes()
        if len(modes) == 1:
            return modes[0]
        return ProviderMode.FRONTIER

    def _build(self, policy: ModelPolicy, *, adapter: Optional[str]) -> Model:
        """Construct the policy's model through the discovery seam.

        Resolution and construction are split so their failures stay distinguishable: an unknown
        registry key is a ``provider_key`` mistake, while a constructor rejecting a value is a
        ``config`` mistake. Catching both under one ``try`` would relabel a backend's own
        ``ValueError("temperature must be between 0 and 2")`` as an unknown provider key and
        point the operator at the wrong field.
        """
        from egpt.registry import discovery

        model_id = policy.model_id
        if model_id is None:  # pragma: no cover -- resolve_detailed checks this first
            raise ModelProviderConfigError("Policy declares no model_id.", field_name="model_id")

        provider_key = policy.effective_provider_key
        try:
            model_cls = discovery.resolve("model", provider_key)
        except ValueError as e:
            # ImportError deliberately propagates unwrapped, matching discovery.resolve: its
            # message names the package to install, and replacing it would discard that.
            raise ModelProviderConfigError(
                f"Unknown provider_key {provider_key!r}: {e}",
                field_name="provider_key",
            ) from e

        config = policy.merged_config()
        if adapter:
            # `resolve_detailed` already reduced this to applied-or-None, so reaching here
            # means a LOCAL policy. How it lands depends on what the backend can consume:
            # a class whose constructor takes `adapter` gets it as a kwarg; for the rest --
            # including the default `vllm` key, whose dataclass has no such field -- the
            # adapter is rendered as the request model id, which is how vLLM serves LoRA
            # (`--lora-modules name=path`; requesting model=<name> answers with base+LoRA).
            # The contract's own ModelResolveRequest.adapter comment documents exactly this
            # rendering. Passing an unknown kwarg instead made every LOCAL+adapter policy on
            # the default key fail at resolution time, which is not a config mistake.
            import inspect

            params = inspect.signature(model_cls).parameters
            takes_adapter = "adapter" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
            if takes_adapter:
                # Assigned, not `setdefault`: an explicit adapter outranks one buried in the
                # opaque `config` blob -- deferring to `config` would report one adapter on
                # the result while different weights answered.
                config["adapter"] = adapter
            else:
                # A stale adapter in the config blob would be forwarded as an unknown kwarg
                # and rejected by the very constructor this branch exists to spare.
                config.pop("adapter", None)
                model_id = adapter

        try:
            model: Model = model_cls(id=model_id, **config)
        except TypeError as e:
            # A backend that does not accept a kwarg the policy supplied ("adapter" on a
            # provider with no LoRA support) is a configuration mistake, not a bug.
            raise ModelProviderConfigError(
                f"Backend {provider_key!r} rejected the policy's config: {e}",
                field_name="config",
            ) from e
        return model

    # -- introspection ------------------------------------------------------

    def capabilities(self) -> ModelCapabilities:
        """What this config can produce. Pure -- reads strings, imports no SDK."""
        modes = self.config.modes()
        return ModelCapabilities(
            modes=modes,
            provider_keys=self.config.provider_keys(),
            supports_adapters=ProviderMode.LOCAL in modes,
            supports_escalation=False,
            egress_free=modes == (ProviderMode.LOCAL,),
        )

    # -- construction -------------------------------------------------------

    @classmethod
    def from_settings(cls, **overrides: Any) -> "ConfigDrivenModelProvider":
        """Build from ``ProviderSettings.policy_file``, falling back to ``default_mode``.

        Sync-only: it reads a local policy document and builds an object, the same rationale
        that makes ``discovery.construct`` sync-only. Nothing here is I/O-bound in the sense
        the both-variants rule targets.

        Raises:
            ModelProviderConfigError: the policy file is missing, unreadable, or invalid.
        """
        import json
        from pathlib import Path

        from egpt.settings.providers import get_provider_settings

        settings = get_provider_settings(**overrides)

        if settings.policy_file:
            path = Path(settings.policy_file)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except OSError as e:
                raise ModelProviderConfigError(
                    f"Could not read the policy file at {path}: {e}",
                    field_name="policy_file",
                ) from e
            except json.JSONDecodeError as e:
                raise ModelProviderConfigError(
                    f"Policy file at {path} is not valid JSON: {e}",
                    field_name="policy_file",
                ) from e
            return cls(ProviderConfig.from_dict(raw))

        if settings.default_mode:
            # No document, but a mode was declared. That IS enough to state a default policy:
            # deployments that pin models in code (`Agent(model=...)`) resolve through the
            # `request.requested` branch and only wanted the mode reported on the result.
            # Raising here made declaring the EGPT-3.1 setting strictly worse than omitting
            # it -- a startup crash for a configuration that serves every pinned request
            # perfectly well. A request that needs configuration to choose a model still
            # fails, per-request, with ModelPolicy's clear missing-model_id error.
            return cls(
                ProviderConfig(default=ModelPolicy(mode=_parse_mode(settings.default_mode, field_name="default_mode")))
            )

        return cls(ProviderConfig())

    def __repr__(self) -> str:
        return f"ConfigDrivenModelProvider(name={self.name!r}, modes={[m.value for m in self.config.modes()]})"
