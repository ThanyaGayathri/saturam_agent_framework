"""Gateway routing for Mode B (EGPT-3.4).

A gateway is a proxy that sits between EGPT and a hosted frontier provider, so that
observability, retries, spend caps and key custody live in one place instead of in every
deployment's environment. Mode B treats "which gateway" as one more configuration value: the
same ``FrontierModelProvider`` resolves either straight to ``anthropic``/``openai`` or through
a gateway, and the only difference the caller sees is the ``provider_key`` and ``gateway``
fields on the returned :class:`~egpt.providers.model_provider.ModelResolveResult`.

This module holds *only* the mapping from a configured gateway name to the registry key and
constructor kwargs it needs. It builds nothing itself: construction goes through
``discovery.construct`` in ``mode_b.py``, so importing this module imports no SDK. That is
required, not stylistic -- ``tests/unit/registry/test_discovery_laziness.py`` runs in a clean
subprocess and fails any module under ``egpt/providers/`` that names a provider class at
import time.

Two gateways, deliberately asymmetric:

* **Portkey** is the supported path. ``portkey-ai`` is a declared extra (``egpt[portkey]``),
  ``Portkey`` extends ``OpenAILike``, and it registers under the key ``"portkey"``. Fully
  implemented and tested.

* **LiteLLM is INERT: code-only, never exercised.** ``egpt[litellm]`` is commented out in
  ``libs/agno/pyproject.toml`` (~line 249): "Temporarily disabled - litellm compromised on
  PyPI (March 24, 2026)". The routing code below exists so that re-enabling the extra is a
  one-line pyproject change rather than a new feature, but **nothing here may install
  ``litellm``, and no test may exercise this path.** While the package is absent, configuring
  ``mode_b_gateway="litellm"`` raises :class:`ModelProviderConfigError` naming the block --
  a clear configuration error rather than a raw ``ImportError`` traceback from deep inside a
  constructor. Do not "fix" that by installing the package.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, Dict, Optional, Tuple

from egpt.providers.model_provider import ModelProviderConfigError

__all__ = [
    "GATEWAY_LITELLM",
    "GATEWAY_PORTKEY",
    "GatewayRoute",
    "known_gateways",
    "resolve_gateway",
]

GATEWAY_PORTKEY = "portkey"
GATEWAY_LITELLM = "litellm"

# Gateway name -> (registry key it constructs through, distribution that must be installed).
# The distribution is checked with ``importlib.util.find_spec``, which does not execute the
# module: a probe must stay as cheap and as side-effect-free as ``capabilities()``.
_GATEWAY_KEYS: Dict[str, Tuple[str, str]] = {
    GATEWAY_PORTKEY: ("portkey", "portkey_ai"),
    # INERT. See the module docstring: egpt[litellm] is disabled in pyproject.toml (~line 249)
    # over the March 2026 PyPI compromise. Present so the routing is not a future feature; not
    # installable and not tested.
    GATEWAY_LITELLM: ("litellm", "litellm"),
}

# Gateways whose dependency is blocked rather than merely absent. A missing optional extra is
# normally "pip install it"; these must not be, so the error says so explicitly instead of
# pointing at an install command that would undo a supply-chain decision.
_BLOCKED_GATEWAYS: Dict[str, str] = {
    GATEWAY_LITELLM: (
        "the `egpt[litellm]` extra is disabled in libs/agno/pyproject.toml (~line 249): "
        "litellm was compromised on PyPI (March 24, 2026). Do not install it to work around "
        "this error -- use `mode_b_gateway='portkey'` or no gateway at all."
    ),
}


@dataclass(frozen=True)
class GatewayRoute:
    """How to build a model that goes through a gateway.

    Frozen for the same reason ``ModelResolveRequest`` is: a route is derived per resolution
    and handed to a constructor, and nothing downstream may edit it in place.

    ``config`` carries only the kwargs the gateway class itself takes. The *frontier* model id
    stays the caller's ``model_id`` -- a gateway is a transport, not a model choice, so routing
    ``claude-sonnet-4`` through Portkey must still ask for ``claude-sonnet-4``.
    """

    name: str
    provider_key: str
    config: Dict[str, Any]


def known_gateways() -> Tuple[str, ...]:
    """Every gateway name this module can route, installed or not.

    Pure and import-free, so ``capabilities()`` can call it. Naming a gateway is not a claim
    that its dependency is present -- ``resolve_gateway`` is what discovers that.
    """
    return tuple(sorted(_GATEWAY_KEYS))


def resolve_gateway(
    name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    virtual_key: Optional[str] = None,
) -> GatewayRoute:
    """Map a configured gateway name to the registry key and kwargs that build it.

    Pure construction planning: it imports no SDK and builds no model, so it is safe on the
    ``capabilities()``-adjacent paths where a missing optional dependency must stay a
    non-event. ``mode_b.py`` feeds the result to ``discovery.construct``.

    Raises:
        ModelProviderConfigError: the name is not a gateway this module knows, or its package
            is not importable. Both are deployment mistakes, and both must surface as a
            configuration error rather than as an ``ImportError`` from inside a constructor --
            EGPT-3.4's "a missing credential/dependency yields a clear configuration error".
    """
    key_and_dist = _GATEWAY_KEYS.get(name)
    if key_and_dist is None:
        known = ", ".join(known_gateways())
        raise ModelProviderConfigError(
            f"Unknown gateway '{name}'. Known gateways: {known}. Leave the gateway unset to "
            "route directly to the frontier provider.",
            field_name="mode_b_gateway",
        )
    provider_key, distribution = key_and_dist

    if find_spec(distribution) is None:
        blocked = _BLOCKED_GATEWAYS.get(name)
        if blocked is not None:
            raise ModelProviderConfigError(
                f"Gateway '{name}' is configured but unavailable: {blocked}",
                field_name="mode_b_gateway",
            )
        raise ModelProviderConfigError(
            f"Gateway '{name}' is configured but the '{distribution}' package is not installed. "
            f"Install it with `pip install egpt[{name}]`.",
            field_name="mode_b_gateway",
        )

    config: Dict[str, Any] = {}
    if name == GATEWAY_PORTKEY:
        # Portkey's own field names. ``portkey_api_key`` is required by its ``_get_client_params``
        # and raises ModelAuthenticationError when unset, so it is checked here too: at
        # resolution time the deployment can still be told which setting is missing.
        if not api_key:
            raise ModelProviderConfigError(
                "Gateway 'portkey' is configured but no gateway API key is set. Set "
                "EGPT_MODE_B_GATEWAY_API_KEY (or PORTKEY_API_KEY).",
                field_name="mode_b_gateway_api_key",
            )
        config["portkey_api_key"] = api_key
        if virtual_key:
            config["virtual_key"] = virtual_key
        if base_url:
            config["base_url"] = base_url
    else:
        # LiteLLM and any future gateway: unreachable while the dependency is blocked, and
        # deliberately left unconfigured rather than guessed at. See the module docstring.
        if api_key:
            config["api_key"] = api_key
        if base_url:
            config["api_base"] = base_url

    return GatewayRoute(name=name, provider_key=provider_key, config=config)
