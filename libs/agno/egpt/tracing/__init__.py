"""
Agno Tracing Module

This module provides OpenTelemetry-based tracing capabilities for Agno agents.
It uses the openinference-instrumentation-agno package for automatic instrumentation
and provides a custom DatabaseSpanExporter to store traces in the Agno database.
"""

from egpt.tracing.exporter import DatabaseSpanExporter
from egpt.tracing.setup import setup_tracing

__all__ = ["DatabaseSpanExporter", "setup_tracing"]
