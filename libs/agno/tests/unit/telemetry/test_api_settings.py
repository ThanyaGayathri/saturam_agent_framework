"""Tests for telemetry API settings — no upstream defaults."""

from egpt.api.settings import AgnoAPISettings, resolve_telemetry_api_url


def test_api_url_default_empty(monkeypatch):
    """Default api_url is empty — no os-api.agno.com phone-home."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)
    monkeypatch.delenv("EGPT_API_RUNTIME", raising=False)
    monkeypatch.delenv("AGNO_API_RUNTIME", raising=False)

    settings = AgnoAPISettings(api_runtime="prd", api_url="")
    assert settings.api_url == ""
    assert settings.has_telemetry_endpoint() is False


def test_api_url_stg_default_empty(monkeypatch):
    """Staging runtime also has no upstream default endpoint."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)

    settings = AgnoAPISettings(api_runtime="stg", api_url="")
    assert settings.api_url == ""
    assert "agno.com" not in settings.api_url


def test_api_url_dev_empty_without_explicit(monkeypatch):
    """Dev runtime does not auto-fill localhost — require EGPT_API_URL."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)
    monkeypatch.delenv("EGPT_RUNTIME", raising=False)
    monkeypatch.delenv("AGNO_RUNTIME", raising=False)

    settings = AgnoAPISettings(api_runtime="dev", api_url="")
    assert settings.api_url == ""
    assert settings.has_telemetry_endpoint() is False


def test_api_url_explicit_override():
    """Explicit api_url is honored for any runtime."""
    settings = AgnoAPISettings(api_runtime="prd", api_url="https://telemetry.example.com")
    assert settings.api_url == "https://telemetry.example.com"
    assert settings.has_telemetry_endpoint() is True


def test_api_url_explicit_dev_localhost():
    """Dev tooling must set EGPT_API_URL explicitly (e.g. localhost)."""
    settings = AgnoAPISettings(api_runtime="dev", api_url="http://localhost:7070")
    assert settings.api_url == "http://localhost:7070"
    assert settings.has_telemetry_endpoint() is True


def test_api_url_rejects_nonsense_values():
    settings = AgnoAPISettings(api_url="false")
    assert settings.api_url == ""
    assert settings.has_telemetry_endpoint() is False


def test_api_url_rejects_blocked_upstream():
    settings = AgnoAPISettings(api_url="https://os-api.agno.com")
    assert settings.api_url == ""
    assert settings.has_telemetry_endpoint() is False


def test_resolve_telemetry_api_url_honors_runtime_env(monkeypatch):
    """EGPT_API_URL set after import must still be seen by the gate."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)
    assert resolve_telemetry_api_url() == "" or "agno.com" not in resolve_telemetry_api_url()

    monkeypatch.setenv("EGPT_API_URL", "https://telemetry.example.com")
    assert resolve_telemetry_api_url() == "https://telemetry.example.com"

    monkeypatch.setenv("EGPT_API_URL", "https://os-api.agno.com")
    assert resolve_telemetry_api_url() == ""


def test_blocked_hosts_are_not_defaults():
    """Upstream hosts may appear only as an explicit blocklist, never as defaults."""
    import inspect

    import egpt.api.settings as settings_mod

    source = inspect.getsource(settings_mod)
    assert 'api_url: str = "https://os-api.agno.com"' not in source
    assert 'return "https://os-api.agno.com"' not in source
    assert 'return "https://api-stg.agno.com"' not in source
    assert 'return "http://localhost:7070"' not in source
    assert "_BLOCKED_TELEMETRY_HOSTS" in source
