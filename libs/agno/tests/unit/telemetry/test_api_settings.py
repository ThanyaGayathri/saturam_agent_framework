"""Tests for telemetry API settings — no upstream defaults."""


def test_api_url_default_empty(monkeypatch):
    """Default api_url is empty — no os-api.agno.com phone-home."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)
    monkeypatch.delenv("EGPT_API_RUNTIME", raising=False)
    monkeypatch.delenv("AGNO_API_RUNTIME", raising=False)

    from egpt.api.settings import AgnoAPISettings

    settings = AgnoAPISettings(api_runtime="prd", api_url="")
    assert settings.api_url == ""
    assert settings.has_telemetry_endpoint() is False


def test_api_url_stg_default_empty(monkeypatch):
    """Staging runtime also has no upstream default endpoint."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)

    from egpt.api.settings import AgnoAPISettings

    settings = AgnoAPISettings(api_runtime="stg", api_url="")
    assert settings.api_url == ""
    assert "agno.com" not in settings.api_url


def test_api_url_dev_empty_without_explicit(monkeypatch):
    """Dev runtime does not auto-fill localhost — require EGPT_API_URL."""
    monkeypatch.delenv("EGPT_API_URL", raising=False)
    monkeypatch.delenv("AGNO_API_URL", raising=False)
    monkeypatch.delenv("EGPT_RUNTIME", raising=False)
    monkeypatch.delenv("AGNO_RUNTIME", raising=False)

    from egpt.api.settings import AgnoAPISettings

    settings = AgnoAPISettings(api_runtime="dev", api_url="")
    assert settings.api_url == ""
    assert settings.has_telemetry_endpoint() is False


def test_api_url_explicit_override():
    """Explicit api_url is honored for any runtime."""
    from egpt.api.settings import AgnoAPISettings

    settings = AgnoAPISettings(api_runtime="prd", api_url="https://telemetry.example.com")
    assert settings.api_url == "https://telemetry.example.com"
    assert settings.has_telemetry_endpoint() is True


def test_api_url_explicit_dev_localhost():
    """Dev tooling must set EGPT_API_URL explicitly (e.g. localhost)."""
    from egpt.api.settings import AgnoAPISettings

    settings = AgnoAPISettings(api_runtime="dev", api_url="http://localhost:7070")
    assert settings.api_url == "http://localhost:7070"
    assert settings.has_telemetry_endpoint() is True


def test_no_upstream_agno_defaults_in_source():
    """Settings module source must not hardcode upstream agno.com endpoints."""
    import inspect

    import egpt.api.settings as settings_mod

    source = inspect.getsource(settings_mod)
    assert "os-api.agno.com" not in source
    assert "api-stg.agno.com" not in source
    assert "localhost:7070" not in source
    assert "host.docker.internal" not in source
