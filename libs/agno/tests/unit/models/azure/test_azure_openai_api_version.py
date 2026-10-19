"""``AzureOpenAI`` pins ``DEFAULT_API_VERSION``; ``resolve_api_version`` is the opt-in chain.

The model never reads ``AZURE_OPENAI_API_VERSION`` by itself: a deployment that exports the
variable (deploy env examples do) must not move every Azure call onto a different version as a
side effect of a framework upgrade. A caller that wants the environment to name the version
passes ``api_version=resolve_api_version()`` explicitly, which also lets it validate a request
field against the version it is about to send (PR #327 review).
"""

import pytest

pytest.importorskip("openai", reason="AzureOpenAI needs the openai SDK")

from egpt.integrations.models.azure.openai_chat import (  # noqa: E402
    DEFAULT_API_VERSION,
    AzureOpenAI,
    resolve_api_version,
)


@pytest.fixture(autouse=True)
def _azure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)


def test_the_default_is_sent_when_nothing_names_a_version() -> None:
    assert AzureOpenAI(id="dep")._get_client_params()["api_version"] == DEFAULT_API_VERSION


def test_the_environment_variable_alone_does_not_change_what_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    assert AzureOpenAI(id="dep")._get_client_params()["api_version"] == DEFAULT_API_VERSION


def test_an_explicit_version_is_sent_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    model = AzureOpenAI(id="dep", api_version="2024-06-01")
    assert model._get_client_params()["api_version"] == "2024-06-01"


def test_resolve_api_version_is_how_a_caller_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_api_version() == DEFAULT_API_VERSION
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "preview")
    assert resolve_api_version() == "preview"
    assert resolve_api_version("2024-06-01") == "2024-06-01"
    model = AzureOpenAI(id="dep", api_version=resolve_api_version())
    assert model._get_client_params()["api_version"] == "preview"
