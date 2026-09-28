"""The Azure Foundry Claude model must not hand an `httpx` client to an httpx2 SDK.

`anthropic` 1.0 moved to **httpx2** and rejects an `httpx` client outright:

    Invalid `http_client` argument; `httpx.AsyncClient` is from the `httpx` package,
    but this SDK uses `httpx2`. Use `httpx2.AsyncClient` instead.

`egpt[anthropic]` is unpinned, so a fresh install resolves 1.x — and this model injected the
framework's shared `httpx` client on *every* construction, including when the caller asked for
no client at all. Every call through it failed on that message, with the SDK's error surfacing
as the agent's answer.
"""

import pytest

pytest.importorskip("anthropic", reason="the Azure Foundry model imports the SDK at module level")

from egpt.integrations.models.azure.claude import (  # noqa: E402
    Claude,
    _sdk_accepts_httpx_client,
)


def test_no_framework_client_is_injected_on_an_httpx2_sdk(monkeypatch):
    """On anthropic >= 1.0 the SDK builds its own client; we pass none."""
    monkeypatch.setattr("egpt.integrations.models.azure.claude._sdk_accepts_httpx_client", lambda: False)
    model = Claude(id="claude-haiku-4-5", api_key="k", resource="r")
    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def is_closed(self):
            return False

    monkeypatch.setattr("egpt.integrations.models.azure.claude.AnthropicFoundry", _Fake)
    model.get_client()
    assert "http_client" not in captured, (
        "an httpx client handed to an httpx2 SDK is rejected, and the rejection reaches the user as the agent's answer"
    )


def test_the_framework_client_is_still_injected_on_an_httpx_sdk(monkeypatch):
    """On anthropic 0.x the shared pool is still used — the fix must not cost that."""
    monkeypatch.setattr("egpt.integrations.models.azure.claude._sdk_accepts_httpx_client", lambda: True)
    model = Claude(id="claude-haiku-4-5", api_key="k", resource="r")
    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def is_closed(self):
            return False

    monkeypatch.setattr("egpt.integrations.models.azure.claude.AnthropicFoundry", _Fake)
    model.get_client()
    assert "http_client" in captured


def test_an_explicit_client_is_passed_through_whatever_the_sdk_uses(monkeypatch):
    """The version check screens the *framework's* default only. A caller who supplies a
    client — an httpx2 one on 1.x, say — keeps it, which is how pooling stays available."""
    monkeypatch.setattr("egpt.integrations.models.azure.claude._sdk_accepts_httpx_client", lambda: False)
    sentinel = object()
    model = Claude(id="claude-haiku-4-5", api_key="k", resource="r", http_client=sentinel)
    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def is_closed(self):
            return False

    monkeypatch.setattr("egpt.integrations.models.azure.claude.AnthropicFoundry", _Fake)
    model.get_client()
    assert captured.get("http_client") is sentinel


def test_the_version_probe_answers_for_the_installed_sdk():
    """Whatever is installed, the probe returns a bool rather than raising — an unreadable
    version must not stop a model being built."""
    assert isinstance(_sdk_accepts_httpx_client(), bool)
