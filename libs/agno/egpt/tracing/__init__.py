"""
EGPT Tracing Module

This module provides OpenTelemetry-based tracing capabilities for EGPT agents.
It uses the openinference-instrumentation-agno package for automatic instrumentation
and provides a custom DatabaseSpanExporter to store traces in the EGPT database.
"""

from egpt.tracing.exporter import DatabaseSpanExporter
from egpt.tracing.memory import memory_span
from egpt.tracing.setup import setup_tracing

__all__ = ["DatabaseSpanExporter", "memory_span", "setup_tracing"]
