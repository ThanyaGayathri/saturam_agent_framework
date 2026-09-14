"""Unit tests for `app/factory.py`'s `_register_exception_handlers`/`_cors_headers_for_error`
(round-2 review, PR #252): the CORS-on-error reflection logic and each handler's status/detail
shape, all pure `Request` in / `JSONResponse` out -- no Postgres, no Neo4j, no real `EgptOS`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import HTTPStatusError, Request as HttpxRequest, Response as HttpxResponse
from starlette.middleware.cors import CORSMiddleware

from egpt_retail.app.factory import _register_exception_handlers

ALLOWED_ORIGIN = "http://localhost:3000"
DISALLOWED_ORIGIN = "http://evil.example"


def _app(*, allow_credentials: bool = True) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ALLOWED_ORIGIN],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_exception_handlers(app)

    @app.get("/boom-generic")
    async def boom_generic():
        raise RuntimeError("connection to postgresql://ai:secret@db/ai failed")

    @app.get("/boom-http")
    async def boom_http():
        raise HTTPException(status_code=404, detail="not found")

    @app.get("/boom-validation")
    async def boom_validation(required: int):
        return {"required": required}

    @app.get("/boom-status")
    async def boom_status():
        raise HTTPStatusError(
            "upstream failed",
            request=HttpxRequest("GET", "http://upstream/"),
            response=HttpxResponse(502, text="bad gateway"),
        )

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


@pytest.fixture
def client_no_credentials() -> TestClient:
    return TestClient(_app(allow_credentials=False), raise_server_exceptions=False)


class TestGeneralExceptionHandler:
    def test_does_not_leak_exception_text(self, client: TestClient):
        """The Major finding: `str(exc)` must never reach the wire."""
        response = client.get("/boom-generic", headers={"origin": ALLOWED_ORIGIN})
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error."}
        assert "secret" not in response.text
        assert "postgresql://" not in response.text

    def test_carries_cors_headers_for_allowed_origin(self, client: TestClient):
        response = client.get("/boom-generic", headers={"origin": ALLOWED_ORIGIN})
        assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
        assert response.headers.get("access-control-allow-credentials") == "true"

    def test_no_cors_headers_for_disallowed_origin(self, client: TestClient):
        response = client.get("/boom-generic", headers={"origin": DISALLOWED_ORIGIN})
        assert "access-control-allow-origin" not in response.headers

    def test_no_cors_headers_without_an_origin(self, client: TestClient):
        response = client.get("/boom-generic")
        assert "access-control-allow-origin" not in response.headers

    def test_omits_credentials_header_when_middleware_disallows_it(
        self, client_no_credentials: TestClient
    ):
        """The Minor finding: don't hardcode `allow-credentials: true`."""
        response = client_no_credentials.get(
            "/boom-generic", headers={"origin": ALLOWED_ORIGIN}
        )
        assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
        assert "access-control-allow-credentials" not in response.headers


class TestOtherHandlersUnaffected:
    """These three run inside `CORSMiddleware` already, so the handler itself adds no CORS
    headers -- verifying that stays true, and that each shape is unchanged."""

    def test_http_exception_shape(self, client: TestClient):
        response = client.get("/boom-http")
        assert response.status_code == 404
        assert response.json() == {"detail": "not found"}

    def test_validation_error_shape(self, client: TestClient):
        response = client.get("/boom-validation")
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_http_status_error_shape(self, client: TestClient):
        response = client.get("/boom-status")
        assert response.status_code == 502
        assert response.json() == {"detail": "bad gateway"}
