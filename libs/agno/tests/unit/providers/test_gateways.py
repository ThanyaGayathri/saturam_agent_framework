"""Gateway routing for Mode B (EGPT-3.4).

``resolve_gateway`` is pure construction planning -- it maps a configured gateway name to a
registry key plus kwargs and imports no SDK -- so almost everything here runs with no optional
dependency installed at all.

**On LiteLLM:** exactly one test below touches the LiteLLM name, and it asserts only that the
guard *degrades cleanly* while the package is absent. The LiteLLM path itself is never
exercised, by deliberate decision: ``egpt[litellm]`` is commented out in
``libs/agno/pyproject.toml`` (~line 249) -- "Temporarily disabled - litellm compromised on PyPI
(March 24, 2026)". **Do not install ``litellm`` to make a richer test possible.** If you are
here because that test looks thin, it is thin on purpose.
"""

from __future__ import annotations

from importlib.util import find_spec

import pytest

from egpt.providers.gateways import (
    GATEWAY_LITELLM,
    GATEWAY_PORTKEY,
    GatewayRoute,
    known_gateways,
    resolve_gateway,
)
from egpt.providers.model_provider import ModelProviderConfigError


def test_known_gateways_lists_both_names():
    """Naming a gateway is not a claim that its dependency is installed."""
    assert known_gateways() == (GATEWAY_LITELLM, GATEWAY_PORTKEY)


def test_portkey_route_carries_configured_settings():
    route = resolve_gateway(
        GATEWAY_PORTKEY,
        api_key="pk-test",
        base_url="http://127.0.0.1:9/v1",
        virtual_key="vk-anthropic",
    )

    assert isinstance(route, GatewayRoute)
    assert route.name == GATEWAY_PORTKEY
    # The registry KEY, asserted without importing portkey_ai.
    assert route.provider_key == "portkey"
    assert route.config == {
        "portkey_api_key": "pk-test",
        "virtual_key": "vk-anthropic",
        "base_url": "http://127.0.0.1:9/v1",
    }


def test_portkey_route_omits_unset_optional_config():
    """An unset virtual key/base_url must not be forwarded as None -- Portkey has its own
    defaults for both, and passing None would override them."""
    route = resolve_gateway(GATEWAY_PORTKEY, api_key="pk-test")
    assert route.config == {"portkey_api_key": "pk-test"}


def test_portkey_without_api_key_is_a_config_error():
    """The gateway key is EGPT-owned config, so its absence is diagnosable here -- unlike a
    frontier credential, which only the vendor SDK can speak to."""
    with pytest.raises(ModelProviderConfigError) as excinfo:
        resolve_gateway(GATEWAY_PORTKEY)

    assert excinfo.value.field_name == "mode_b_gateway_api_key"
    assert "PORTKEY_API_KEY" in str(excinfo.value)


def test_unknown_gateway_names_the_known_ones():
    with pytest.raises(ModelProviderConfigError) as excinfo:
        resolve_gateway("not-a-gateway", api_key="x")

    assert excinfo.value.field_name == "mode_b_gateway"
    message = str(excinfo.value)
    assert GATEWAY_PORTKEY in message
    assert "not-a-gateway" in message


def test_route_is_frozen():
    """Routes are derived per resolution and handed to a constructor; nothing may edit one."""
    route = resolve_gateway(GATEWAY_PORTKEY, api_key="pk-test")
    with pytest.raises(Exception):
        route.provider_key = "openai"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# The one LiteLLM test. Read the module docstring before adding a second.
# --------------------------------------------------------------------------- #


def test_litellm_gateway_degrades_to_a_config_error_while_blocked():
    """LiteLLM routing is INERT: code-only, never exercised.

    ``egpt[litellm]`` is disabled in ``libs/agno/pyproject.toml`` (~line 249) over the March 24,
    2026 PyPI compromise. This asserts the *guard*, not the path: configuring the gateway while
    the package is absent must produce a ``ModelProviderConfigError`` naming the blocked
    dependency, not a raw ``ImportError`` traceback from inside a constructor.

    **Do not install ``litellm`` to "un-skip" this.** If the package is ever legitimately
    re-enabled, this test skips itself rather than turning into a green test of an untested
    path -- and re-enabling it is a pyproject decision, not a test-file one.
    """
    if find_spec("litellm") is not None:
        pytest.skip("litellm is installed; the blocked-dependency guard does not apply")

    with pytest.raises(ModelProviderConfigError) as excinfo:
        resolve_gateway(GATEWAY_LITELLM, api_key="x")

    assert excinfo.value.field_name == "mode_b_gateway"
    message = str(excinfo.value)
    assert "litellm" in message
    assert "compromised on PyPI" in message
    # The error must not push the reader toward undoing the supply-chain decision.
    assert "pip install litellm" not in message
