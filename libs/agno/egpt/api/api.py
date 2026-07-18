from __future__ import annotations

from typing import Any, Dict

from httpx import AsyncClient as HttpxAsyncClient
from httpx import Client as HttpxClient
from httpx import Response

from egpt.api.settings import agno_api_settings, resolve_telemetry_api_url


class _NoOpTelemetryClient:
    """No-op sync client when no telemetry endpoint is configured.

    Guarantees zero egress even if a caller forgets the endpoint guard.
    """

    def __enter__(self) -> "_NoOpTelemetryClient":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def post(self, *args: Any, **kwargs: Any) -> None:
        return None


class _NoOpAsyncTelemetryClient:
    """No-op async client when no telemetry endpoint is configured."""

    async def __aenter__(self) -> "_NoOpAsyncTelemetryClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(self, *args: Any, **kwargs: Any) -> None:
        return None


class Api:
    def __init__(self):
        self.headers: Dict[str, str] = {
            "user-agent": f"{agno_api_settings.app_name}/{agno_api_settings.app_version}",
            "Content-Type": "application/json",
        }

    def Client(self) -> Any:
        """Return an httpx client, or a no-op when no telemetry endpoint is set."""
        base_url = resolve_telemetry_api_url()
        if not base_url:
            return _NoOpTelemetryClient()
        return HttpxClient(
            base_url=base_url,
            headers=self.headers,
            timeout=60,
            http2=True,
        )

    def AsyncClient(self) -> Any:
        """Return an async httpx client, or a no-op when no telemetry endpoint is set."""
        base_url = resolve_telemetry_api_url()
        if not base_url:
            return _NoOpAsyncTelemetryClient()
        return HttpxAsyncClient(
            base_url=base_url,
            headers=self.headers,
            timeout=60,
            http2=True,
        )


api = Api()


def is_telemetry_endpoint_configured() -> bool:
    """Return True when a telemetry API endpoint is configured (live env aware)."""
    return bool(resolve_telemetry_api_url())


def invalid_response(r: Response) -> bool:
    """Returns true if the response is invalid"""

    if r.status_code >= 400:
        return True
    return False
