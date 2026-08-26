"""
Setup helper functions for configuring EGPT tracing.

Supports two export backends (can run simultaneously):
- **Database exporter** (existing): writes spans to EGPT's own database.
- **Langfuse exporter** (EGPT-4.1): exports spans to Langfuse via OTLP/HTTP
  when keys are configured, with zero additional setup required.
"""

import base64
from typing import Optional, Union

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.remote.base import RemoteDb
from egpt.settings.tracing import TracingSettings, get_tracing_settings
from egpt.tracing.exporter import DatabaseSpanExporter
from egpt.utils.log import log_debug, log_error, log_info, log_warning

try:
    from opentelemetry import trace as trace_api  # type: ignore
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanProcessor  # type: ignore

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False

try:
    from openinference.instrumentation.agno import AgnoInstrumentor  # type: ignore

    INSTRUMENTOR_AVAILABLE = True
except ImportError:
    INSTRUMENTOR_AVAILABLE = False


def _create_langfuse_processor(
    settings: TracingSettings,
    max_queue_size: int = 2048,
    max_export_batch_size: int = 512,
    schedule_delay_millis: int = 5000,
) -> "Optional[SpanProcessor]":
    """Create a Langfuse OTLP/HTTP span processor if configured.

    Returns a BatchSpanProcessor wrapping an OTLPSpanExporter pointed at the
    Langfuse OTLP HTTP endpoint, or None if Langfuse is not configured/available.
    """
    if not settings.langfuse_is_configured:
        log_debug("Langfuse not configured (missing keys or explicitly disabled)")
        return None

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore
    except ImportError:
        log_warning(
            "Langfuse keys are configured but opentelemetry-exporter-otlp is not installed. "
            "Install with: pip install opentelemetry-exporter-otlp "
            "or pip install egpt[langfuse]. Skipping Langfuse export."
        )
        return None

    # Build the OTLP endpoint URL for Langfuse.
    # Langfuse accepts OTLP/HTTP at <host>/api/public/otel/v1/traces
    langfuse_host = settings.langfuse_host or "https://cloud.langfuse.com"
    otlp_endpoint = f"{langfuse_host.rstrip('/')}/api/public/otel/v1/traces"

    # Langfuse uses HTTP header-based auth for OTLP ingestion:
    # Authorization: Basic base64(public_key:secret_key)
    # langfuse_is_configured guarantees both keys are present.
    assert settings.langfuse_secret_key is not None
    credentials = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key.get_secret_value()}"
    auth_header = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
    }

    try:
        exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint,
            headers=headers,
        )

        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=max_queue_size,
            max_export_batch_size=max_export_batch_size,
            schedule_delay_millis=schedule_delay_millis,
        )

        log_info(f"Langfuse OTLP exporter configured (endpoint: {otlp_endpoint})")
        return processor

    except Exception as e:
        log_error(f"Failed to create Langfuse exporter for {otlp_endpoint}: {e}")
        return None


def setup_tracing(
    db: Optional[Union[BaseDb, AsyncBaseDb, RemoteDb]] = None,
    batch_processing: bool = False,
    max_queue_size: int = 2048,
    max_export_batch_size: int = 512,
    schedule_delay_millis: int = 5000,
) -> None:
    """
    Set up OpenTelemetry tracing with database and/or Langfuse export.

    This function configures automatic tracing for all EGPT agents, teams,
    and workflows. Traces are automatically captured for:
    - Agent runs (agent.run, agent.arun)
    - Model calls (model.response)
    - Tool executions
    - Team coordination
    - Workflow steps

    Export backends (can run simultaneously):
    - Database: when ``db`` is provided and ``tracing_db_enabled`` is True
    - Langfuse: when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set

    At least one backend must be available, or tracing is not set up.

    Args:
        db: Database instance to store traces (sync or async). Optional if
            Langfuse is configured as the sole backend.
        batch_processing: If True, use BatchSpanProcessor for the DB exporter.
            If False, use SimpleSpanProcessor (immediate export).
        max_queue_size: Maximum queue size for batch processors (DB and Langfuse).
        max_export_batch_size: Maximum batch size for export (DB and Langfuse).
        schedule_delay_millis: Delay in ms between batch exports (DB and Langfuse).

    Raises:
        ImportError: If OpenTelemetry packages are not installed.

    Example:
        ```python
        from egpt.integrations.db.sqlite import SqliteDb
        from egpt.tracing import setup_tracing

        # Both DB and Langfuse (if keys are set)
        db = SqliteDb(db_file="tmp/traces.db")
        setup_tracing(db=db)

        # Langfuse only (no local DB storage)
        setup_tracing()
        ```
    """
    if not OPENTELEMETRY_AVAILABLE:
        raise ImportError(
            "OpenTelemetry packages are required for tracing. "
            "Install with: pip install opentelemetry-api opentelemetry-sdk"
        )

    # Check if tracing is already set up (handles reload scenarios)
    current_provider = trace_api.get_tracer_provider()
    if isinstance(current_provider, TracerProvider):
        # Already configured with a real TracerProvider, skip
        return

    try:
        settings = get_tracing_settings()

        # Create tracer provider
        tracer_provider = TracerProvider()
        processors_added = 0

        # --- DB exporter (existing behavior) ---
        if db is not None and settings.tracing_db_enabled:
            exporter = DatabaseSpanExporter(db=db)
            processor: SpanProcessor
            if batch_processing:
                processor = BatchSpanProcessor(
                    exporter,
                    max_queue_size=max_queue_size,
                    max_export_batch_size=max_export_batch_size,
                    schedule_delay_millis=schedule_delay_millis,
                )
                log_debug(
                    f"DB tracing configured with BatchSpanProcessor "
                    f"(queue_size={max_queue_size}, batch_size={max_export_batch_size})"
                )
            else:
                processor = SimpleSpanProcessor(exporter)
                log_debug("DB tracing configured with SimpleSpanProcessor")

            tracer_provider.add_span_processor(processor)
            processors_added += 1
        elif db is not None and not settings.tracing_db_enabled:
            log_info("db= was provided but EGPT_TRACING_DB_ENABLED is false; skipping the database exporter")

        # --- Langfuse OTLP exporter (EGPT-4.1) ---
        langfuse_processor = _create_langfuse_processor(
            settings,
            max_queue_size=max_queue_size,
            max_export_batch_size=max_export_batch_size,
            schedule_delay_millis=schedule_delay_millis,
        )
        if langfuse_processor is not None:
            tracer_provider.add_span_processor(langfuse_processor)
            processors_added += 1

        # --- Validate at least one exporter is active ---
        if processors_added == 0:
            log_warning(
                "No tracing exporters configured. Provide a db argument and/or "
                "set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY environment variables."
            )
            return

        # --- Validate instrumentation is available ---
        # Without the instrumentor, nothing wraps Agent.run / model calls / tools,
        # so no spans are ever created and exporters sit idle. Do not claim the
        # global TracerProvider (it's once-only and unrecoverable) in this state.
        if not INSTRUMENTOR_AVAILABLE:
            log_error(
                "openinference-instrumentation-agno is not installed; no spans would be emitted. "
                "Install with: pip install openinference-instrumentation-agno "
                "or pip install egpt[langfuse]. Tracing not set up."
            )
            return

        # Set the global tracer provider
        trace_api.set_tracer_provider(tracer_provider)

        # Instrument EGPT with OpenInference.
        # skip_dep_check=True is required because the instrumentor hardcodes
        # a dependency check against the upstream package name (not "egpt").
        # Without this flag, instrument() silently returns without wrapping.
        instrumentor = AgnoInstrumentor()
        instrumentor.instrument(tracer_provider=tracer_provider, skip_dep_check=True)

        if not instrumentor.is_instrumented_by_opentelemetry:
            log_error(
                "AgnoInstrumentor.instrument() completed but is_instrumented_by_opentelemetry "
                "is False -- tracing may not be active. Check that "
                "openinference-instrumentation-agno is installed and compatible."
            )

        backends = []
        if db is not None and settings.tracing_db_enabled:
            backends.append("database")
        if langfuse_processor is not None:
            backends.append("Langfuse")

        log_info(f"EGPT tracing set up with: {', '.join(backends)}")

    except Exception as e:
        log_error(f"Failed to set up tracing: {str(e)}")
        raise
