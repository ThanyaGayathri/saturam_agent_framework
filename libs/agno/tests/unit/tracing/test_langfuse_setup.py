"""
Tests for EGPT-4.1: Langfuse tracing wiring.

Verifies that setup_tracing() correctly configures Langfuse export when keys
are present, skips it when keys are absent, and respects the disable toggle.
All tests use mocks — no real Langfuse connection is made.
"""

from __future__ import annotations

import base64
import logging
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("opentelemetry.sdk", reason="tracing tests require opentelemetry-sdk")

import importlib.util

_has_otlp_http = importlib.util.find_spec("opentelemetry.exporter.otlp.proto.http") is not None


@pytest.fixture(autouse=True)
def _clean_tracing_env(monkeypatch):
    """Remove all tracing-related env vars before each test to prevent ambient leaks."""
    for prefix in ("", "EGPT_", "AGNO_"):
        for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_ENABLED"):
            monkeypatch.delenv(f"{prefix}{name}", raising=False)
    monkeypatch.delenv("EGPT_TRACING_DB_ENABLED", raising=False)


@pytest.fixture(autouse=True)
def _reset_global_tracer(monkeypatch):
    """Reset the global tracer provider before AND after each test.

    Forces None both ways to avoid leaking a TracerProvider into subsequent
    test files running in the same pytest worker.
    """
    from opentelemetry import trace as trace_api

    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", trace_api._TRACER_PROVIDER_SET_ONCE.__class__())
    yield
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", trace_api._TRACER_PROVIDER_SET_ONCE.__class__())


class TestLangfuseSettingsResolution:
    """Verify TracingSettings correctly resolves Langfuse configuration."""

    def test_langfuse_not_configured_when_keys_absent(self):
        """Without keys, langfuse_is_configured is False."""
        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        assert settings.langfuse_is_configured is False

    def test_langfuse_configured_when_keys_present(self, monkeypatch):
        """With both keys set, langfuse_is_configured is True."""
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")

        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        assert settings.langfuse_is_configured is True

    def test_langfuse_configured_with_egpt_prefix(self, monkeypatch):
        """EGPT_ prefixed vars also work."""
        monkeypatch.setenv("EGPT_LANGFUSE_PUBLIC_KEY", "pk-egpt-123")
        monkeypatch.setenv("EGPT_LANGFUSE_SECRET_KEY", "sk-egpt-456")

        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        assert settings.langfuse_is_configured is True

    def test_langfuse_disabled_explicitly(self, monkeypatch):
        """When EGPT_LANGFUSE_ENABLED=false, langfuse_is_configured is False."""
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")
        monkeypatch.setenv("EGPT_LANGFUSE_ENABLED", "false")

        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        assert settings.langfuse_is_configured is False

    def test_langfuse_host_defaults_to_none(self):
        """Without LANGFUSE_HOST, host is None (setup.py defaults to cloud)."""
        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        assert settings.langfuse_host is None

    def test_langfuse_host_custom(self, monkeypatch):
        """Custom LANGFUSE_HOST is respected."""
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.mycompany.com")

        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        assert settings.langfuse_host == "https://langfuse.mycompany.com"

    def test_secret_key_is_secret_str(self, monkeypatch):
        """Secret key is stored as SecretStr (not leaked in repr/logs)."""
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-should-not-leak")

        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        # repr should not contain the secret value
        assert "sk-should-not-leak" not in repr(settings)
        # But we can still get the value when needed
        assert settings.langfuse_secret_key is not None
        assert settings.langfuse_secret_key.get_secret_value() == "sk-should-not-leak"

    def test_db_exporter_enabled_by_default(self):
        """DB exporter is enabled by default."""
        from egpt.settings.tracing import get_tracing_settings

        settings = get_tracing_settings()
        assert settings.tracing_db_enabled is True


@pytest.mark.skipif(not _has_otlp_http, reason="opentelemetry-exporter-otlp not installed")
class TestSetupTracingLangfuse:
    """Verify setup_tracing() wires Langfuse when configured.

    Uses monkeypatch.setattr(..., raising=False) for AgnoInstrumentor so
    these tests run even when openinference is not installed.
    """

    @pytest.fixture(autouse=True)
    def _mock_instrumentor(self, monkeypatch):
        """Mock AgnoInstrumentor and INSTRUMENTOR_AVAILABLE to avoid the agno module dependency."""
        mock_inst = MagicMock()
        mock_inst.is_instrumented_by_opentelemetry = True
        monkeypatch.setattr("egpt.tracing.setup.INSTRUMENTOR_AVAILABLE", True, raising=False)
        monkeypatch.setattr("egpt.tracing.setup.AgnoInstrumentor", lambda: mock_inst, raising=False)

    def test_langfuse_wired_with_correct_endpoint_and_auth(self, monkeypatch, tmp_path):
        """With Langfuse keys set, OTLP HTTP exporter is created with correct
        endpoint path and Basic auth header."""
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")
        monkeypatch.setenv("LANGFUSE_HOST", "https://lf.example.com")

        mock_otlp_cls = MagicMock()
        with patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            mock_otlp_cls,
        ):
            from egpt.tracing.setup import setup_tracing

            setup_tracing()

        # Verify OTLP exporter was called with correct endpoint
        mock_otlp_cls.assert_called_once()
        kwargs = mock_otlp_cls.call_args[1]
        assert kwargs["endpoint"] == "https://lf.example.com/api/public/otel/v1/traces"

        # Verify auth header
        expected_creds = base64.b64encode(b"pk-test-123:sk-test-456").decode()
        assert kwargs["headers"]["Authorization"] == f"Basic {expected_creds}"

    def test_langfuse_defaults_to_cloud_endpoint(self, monkeypatch):
        """Without LANGFUSE_HOST, endpoint defaults to cloud.langfuse.com."""
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        mock_otlp_cls = MagicMock()
        with patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            mock_otlp_cls,
        ):
            from egpt.tracing.setup import setup_tracing

            setup_tracing()

        kwargs = mock_otlp_cls.call_args[1]
        assert kwargs["endpoint"] == "https://cloud.langfuse.com/api/public/otel/v1/traces"

    def test_both_db_and_langfuse_processors(self, monkeypatch, tmp_path):
        """With both DB and Langfuse configured, two processors are added."""
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")

        mock_otlp_cls = MagicMock()
        with patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            mock_otlp_cls,
        ):
            from egpt.integrations.db.sqlite import SqliteDb
            from egpt.tracing.setup import setup_tracing

            db = SqliteDb(db_file=str(tmp_path / "test.db"))
            setup_tracing(db=db)

        provider = trace_api.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        assert len(provider._active_span_processor._span_processors) == 2

    def test_langfuse_only_no_db(self, monkeypatch):
        """With Langfuse keys but no db, only Langfuse processor runs."""
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")

        mock_otlp_cls = MagicMock()
        with patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            mock_otlp_cls,
        ):
            from egpt.tracing.setup import setup_tracing

            setup_tracing()

        provider = trace_api.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        assert len(provider._active_span_processor._span_processors) == 1

    def test_langfuse_disabled_via_toggle(self, monkeypatch, tmp_path):
        """With keys but EGPT_LANGFUSE_ENABLED=false, only DB exporter runs."""
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")
        monkeypatch.setenv("EGPT_LANGFUSE_ENABLED", "false")

        from egpt.integrations.db.sqlite import SqliteDb
        from egpt.tracing.setup import setup_tracing

        db = SqliteDb(db_file=str(tmp_path / "test.db"))
        setup_tracing(db=db)

        provider = trace_api.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        # Only DB, no Langfuse
        assert len(provider._active_span_processor._span_processors) == 1

    def test_no_exporters_warns_and_no_provider(self, caplog):
        """With no db and no Langfuse keys, setup warns and does not set provider."""
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        from egpt.tracing.setup import setup_tracing

        with caplog.at_level(logging.WARNING):
            setup_tracing()

        assert "No tracing exporters configured" in caplog.text
        assert not isinstance(trace_api.get_tracer_provider(), TracerProvider)

    def test_db_disabled_logs_info(self, monkeypatch, tmp_path, caplog):
        """When db is provided but EGPT_TRACING_DB_ENABLED=false, logs info."""
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        monkeypatch.setenv("EGPT_TRACING_DB_ENABLED", "false")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        mock_otlp_cls = MagicMock()
        with patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            mock_otlp_cls,
        ):
            from egpt.integrations.db.sqlite import SqliteDb
            from egpt.tracing.setup import setup_tracing

            db = SqliteDb(db_file=str(tmp_path / "test.db"))
            with caplog.at_level(logging.INFO):
                setup_tracing(db=db)

        assert "EGPT_TRACING_DB_ENABLED is false" in caplog.text
        provider = trace_api.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        # Only Langfuse processor, DB skipped
        assert len(provider._active_span_processor._span_processors) == 1

    def test_missing_otlp_package_falls_back_to_db(self, monkeypatch, tmp_path):
        """If opentelemetry-exporter-otlp is not installed, falls back to DB only."""
        import sys

        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        # Block the OTLP HTTP exporter module so the function-local import raises ImportError
        monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None)

        from egpt.integrations.db.sqlite import SqliteDb
        from egpt.tracing.setup import setup_tracing

        db = SqliteDb(db_file=str(tmp_path / "test.db"))
        setup_tracing(db=db)

        provider = trace_api.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        # Only DB processor
        assert len(provider._active_span_processor._span_processors) == 1

    def test_instrumentor_unavailable_does_not_claim_tracer_provider(self, monkeypatch, tmp_path):
        """When INSTRUMENTOR_AVAILABLE is False, setup_tracing must return before
        claiming the global TracerProvider -- it is once-only and unrecoverable."""
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        # Override the autouse _mock_instrumentor by setting INSTRUMENTOR_AVAILABLE=False
        monkeypatch.setattr("egpt.tracing.setup.INSTRUMENTOR_AVAILABLE", False)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        mock_otlp_cls = MagicMock()
        with patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            mock_otlp_cls,
        ):
            from egpt.integrations.db.sqlite import SqliteDb
            from egpt.tracing.setup import setup_tracing

            db = SqliteDb(db_file=str(tmp_path / "test.db"))
            setup_tracing(db=db)

        # Global provider must NOT be set (instrumentor absent = no spans would emit)
        assert not isinstance(trace_api.get_tracer_provider(), TracerProvider)
