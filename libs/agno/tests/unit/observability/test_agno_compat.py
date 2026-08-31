"""Tests for the production agno→egpt sys.modules aliasing (aicoe-tracker#200).

Each test strips agno.* aliases before running, then calls
``ensure_agno_aliases()`` directly, so assertions are driven by the
production shim — not by pre-existing state from the conftest.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("openinference", reason="requires openinference-instrumentation-agno")
pytest.importorskip("opentelemetry.sdk", reason="requires opentelemetry-sdk")


@pytest.fixture(autouse=True)
def _isolate_aliases():
    """Strip all agno.* aliases and reset _installed before each test.

    Teardown unconditionally resets then re-installs so the rest of the
    observability suite (which depends on conftest-installed aliases) works.
    """
    from egpt.tracing._agno_compat import _reset_for_testing, ensure_agno_aliases

    _reset_for_testing()
    yield
    # Always reset first (clears _installed flag + aliases), then re-install.
    # Cannot rely on ensure_agno_aliases() alone because _installed may
    # already be True from the test body (e.g. test_does_not_clobber).
    _reset_for_testing()
    ensure_agno_aliases()


class TestEnsureAgnoAliases:
    """Verify ensure_agno_aliases registers the expected sys.modules entries."""

    def test_aliases_absent_before_call(self):
        """After _reset_for_testing, agno should not be in sys.modules."""
        assert "agno" not in sys.modules
        assert "agno.agent" not in sys.modules

    def test_agno_root_is_egpt_after_call(self):
        from egpt.tracing._agno_compat import ensure_agno_aliases

        ensure_agno_aliases()

        import egpt

        assert "agno" in sys.modules
        assert sys.modules["agno"] is egpt

    def test_agno_submodules_resolve(self):
        from egpt.tracing._agno_compat import ensure_agno_aliases

        ensure_agno_aliases()

        expected_aliases = [
            "agno.agent",
            "agno.agent._run",
            "agno.agent.agent",
            "agno.models",
            "agno.models.base",
            "agno.tools",
            "agno.tools.function",
            "agno.team",
            "agno.team._run",
        ]

        for alias in expected_aliases:
            assert alias in sys.modules, f"Missing alias: {alias}"
            egpt_name = "egpt." + alias[5:]
            assert sys.modules[alias] is sys.modules[egpt_name], f"{alias} does not point to {egpt_name}"

    def test_scoped_to_instrumentor_prefixes(self):
        """Only instrumentor-relevant prefixes should be aliased, not all egpt.*."""
        from egpt.tracing._agno_compat import ensure_agno_aliases

        ensure_agno_aliases()

        assert "agno.tracing" not in sys.modules
        assert "agno.prompts" not in sys.modules
        assert "agno.settings" not in sys.modules

    def test_idempotent(self):
        """Calling ensure_agno_aliases twice produces the same result."""
        from egpt.tracing._agno_compat import ensure_agno_aliases

        # Before first call: no aliases
        assert "agno" not in sys.modules

        ensure_agno_aliases()
        aliases_after_first = {k for k in sys.modules if k.startswith("agno")}
        assert len(aliases_after_first) > 0, "First call should register aliases"

        ensure_agno_aliases()
        aliases_after_second = {k for k in sys.modules if k.startswith("agno")}
        assert aliases_after_first == aliases_after_second

    def test_skips_entirely_when_foreign_agno_present(self):
        """If a real 'agno' is already in sys.modules, the entire pass is skipped."""
        from egpt.tracing._agno_compat import ensure_agno_aliases

        sentinel = type(sys)("agno")  # fake module
        sys.modules["agno"] = sentinel

        ensure_agno_aliases()

        # Root should NOT have been overwritten
        assert sys.modules["agno"] is sentinel
        # Submodule aliases should NOT be registered either
        assert "agno.agent" not in sys.modules
        assert "agno.tools" not in sys.modules


class TestInstrumentorWithAlias:
    """Verify AgnoInstrumentor can import from agno.* after aliasing."""

    def test_instrumentor_imports_resolve(self):
        from egpt.tracing._agno_compat import ensure_agno_aliases

        ensure_agno_aliases()

        from agno.agent import _run as agent_run_module  # noqa: F401
        from agno.team import _run as team_run_module  # noqa: F401
        from agno.tools.function import FunctionCall  # noqa: F401

    def test_instrumentor_instrument_succeeds(self):
        """Full instrument() call succeeds with aliases registered."""
        from egpt.tracing._agno_compat import ensure_agno_aliases

        ensure_agno_aliases()

        from openinference.instrumentation.agno import AgnoInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        from egpt.tracing.exporter import DatabaseSpanExporter

        provider = TracerProvider()
        mock_db = type("FakeDb", (), {"upsert_trace": lambda *a: None, "upsert_spans": lambda *a: None})()
        provider.add_span_processor(SimpleSpanProcessor(DatabaseSpanExporter(db=mock_db)))

        instrumentor = AgnoInstrumentor()
        instrumentor.instrument(tracer_provider=provider, skip_dep_check=True)

        assert instrumentor.is_instrumented_by_opentelemetry

        instrumentor.uninstrument()


class TestStickyFailureFix:
    """Verify setup_tracing does not claim the global TracerProvider on failure."""

    def test_failed_instrument_does_not_claim_provider(self):
        """If instrument() fails, the global TracerProvider must NOT be set.

        This is the sticky-failure bug from #200: claiming the once-only
        global provider before instrumentation succeeds permanently blocks
        a later successful retry.
        """
        from egpt.tracing._agno_compat import ensure_agno_aliases

        ensure_agno_aliases()

        # Mock AgnoInstrumentor to simulate failure
        mock_instrumentor = MagicMock()
        mock_instrumentor.is_instrumented_by_opentelemetry = False

        with (
            patch("egpt.tracing.setup.AgnoInstrumentor", return_value=mock_instrumentor),
            patch("egpt.tracing.setup.INSTRUMENTOR_AVAILABLE", True),
            patch("egpt.tracing.setup.OPENTELEMETRY_AVAILABLE", True),
        ):
            from egpt.tracing.setup import setup_tracing

            # Force a fresh setup attempt by resetting the check
            with patch("egpt.tracing.setup.trace_api") as mock_trace_api:
                mock_trace_api.get_tracer_provider.return_value = MagicMock()  # not a TracerProvider
                mock_trace_api.set_tracer_provider = MagicMock()

                mock_settings = MagicMock()
                mock_settings.tracing_db_enabled = False
                mock_settings.langfuse_is_configured = True
                mock_settings.langfuse_host = "https://fake.example.com"
                mock_settings.langfuse_public_key = "pk"
                mock_settings.langfuse_secret_key = MagicMock()
                mock_settings.langfuse_secret_key.get_secret_value.return_value = "sk"

                with patch("egpt.tracing.setup.get_tracing_settings", return_value=mock_settings):
                    with patch("egpt.tracing.setup._create_langfuse_processor", return_value=MagicMock()):
                        setup_tracing()

                # instrument() was called
                mock_instrumentor.instrument.assert_called_once()
                # But since is_instrumented_by_opentelemetry is False,
                # set_tracer_provider should NOT have been called
                mock_trace_api.set_tracer_provider.assert_not_called()
