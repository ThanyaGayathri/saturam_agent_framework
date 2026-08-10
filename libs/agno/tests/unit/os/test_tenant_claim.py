"""Resolving the caller's tenant from a JWT claim (EGPT-2.3).

The minimal tenant plumbing this ticket adds: an operator names a claim via
`AuthorizationConfig(tenant_id_claim=...)`, the middleware puts it on `request.state.tenant_id`,
and `egpt.os.tenancy` is the only thing that reads it back.

The important negative case is `_extract_tenant_id` raising on a malformed claim rather than
returning None: None means *unscoped*, which is the widest tenancy there is, so degrading to it
would turn a bad claim into an escalation.
"""

import jwt
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from egpt.os.config import AuthorizationConfig
from egpt.os.middleware import JWTMiddleware
from egpt.os.middleware.jwt import build_jwt_middleware_kwargs
from egpt.os.tenancy import current_tenant_id, get_tenant_id

JWT_SECRET = "test-secret-key-for-tenant-claim-tests"


@pytest.fixture
def middleware():
    return JWTMiddleware(
        app=None,
        verification_keys=[JWT_SECRET],
        algorithm="HS256",
        tenant_id_claim="tid",
    )


# =============================================================================
# Claim extraction
# =============================================================================


class TestExtractTenantId:
    def test_a_string_claim_is_the_tenant(self, middleware):
        assert middleware._extract_tenant_id({"tid": "acme"}) == "acme"

    def test_surrounding_whitespace_is_trimmed(self, middleware):
        assert middleware._extract_tenant_id({"tid": "  acme  "}) == "acme"

    def test_an_absent_claim_is_unscoped(self, middleware):
        assert middleware._extract_tenant_id({"sub": "user-1"}) is None

    def test_no_claim_configured_means_no_tenant_is_ever_resolved(self):
        no_claim = JWTMiddleware(app=None, verification_keys=[JWT_SECRET], algorithm="HS256")
        assert no_claim._extract_tenant_id({"tid": "acme"}) is None

    @pytest.mark.parametrize("value", [["acme"], {"id": "acme"}, 42, "", "   ", True])
    def test_an_unusable_claim_raises_rather_than_falling_back_to_unscoped(self, middleware, value):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            middleware._extract_tenant_id({"tid": value})


# =============================================================================
# Config plumbing
# =============================================================================


class TestMiddlewareKwargs:
    def test_the_claim_is_forwarded_when_configured(self):
        kwargs = build_jwt_middleware_kwargs(AuthorizationConfig(tenant_id_claim="org_id"), authorization=True)
        assert kwargs["tenant_id_claim"] == "org_id"

    def test_the_key_is_omitted_when_unset(self):
        """Matches how audience/admin_scope are handled, so manual setups keep their defaults."""
        assert "tenant_id_claim" not in build_jwt_middleware_kwargs(AuthorizationConfig(), authorization=True)

    def test_no_authorization_config_at_all_omits_it(self):
        assert "tenant_id_claim" not in build_jwt_middleware_kwargs(None, authorization=True)

    def test_the_default_is_none(self):
        assert AuthorizationConfig().tenant_id_claim is None


# =============================================================================
# Reading it back off the request
# =============================================================================


class TestCurrentTenantId:
    def _request_with(self, **state):
        app = FastAPI()

        @app.middleware("http")
        async def seed(request: Request, call_next):
            for key, value in state.items():
                setattr(request.state, key, value)
            return await call_next(request)

        @app.get("/t")
        async def read(tenant_id: str = Depends(get_tenant_id)):
            return {"tenant_id": tenant_id}

        return TestClient(app).get("/t").json()["tenant_id"]

    def test_it_reads_the_attribute(self):
        assert self._request_with(tenant_id="acme") == "acme"

    def test_a_request_with_no_middleware_at_all_is_unscoped(self):
        """The components router's own unit tests mount a bare app, so this must not raise."""
        assert self._request_with() is None

    @pytest.mark.parametrize("value", [None, "", 42, ["acme"]])
    def test_anything_unusable_reads_as_unscoped(self, value):
        assert self._request_with(tenant_id=value) is None

    def test_none_request_is_unscoped(self):
        assert current_tenant_id(None) is None


# =============================================================================
# End to end through the middleware
# =============================================================================


class TestThroughTheMiddleware:
    def _client(self, **middleware_kwargs):
        app = FastAPI()

        @app.get("/t")
        async def read(tenant_id: str = Depends(get_tenant_id)):
            return {"tenant_id": tenant_id}

        app.add_middleware(JWTMiddleware, verification_keys=[JWT_SECRET], algorithm="HS256", **middleware_kwargs)
        return TestClient(app)

    def _token(self, **claims):
        return jwt.encode({"sub": "user-1", **claims}, JWT_SECRET, algorithm="HS256")

    def test_the_claim_reaches_the_handler(self):
        response = self._client(tenant_id_claim="tid").get(
            "/t", headers={"Authorization": f"Bearer {self._token(tid='acme')}"}
        )
        assert response.status_code == 200
        assert response.json()["tenant_id"] == "acme"

    def test_a_token_without_the_claim_is_unscoped(self):
        response = self._client(tenant_id_claim="tid").get("/t", headers={"Authorization": f"Bearer {self._token()}"})
        assert response.status_code == 200
        assert response.json()["tenant_id"] is None

    def test_an_unconfigured_claim_leaves_every_caller_unscoped(self):
        """The no-op path: a deployment that has not opted in is unaffected."""
        response = self._client().get("/t", headers={"Authorization": f"Bearer {self._token(tid='acme')}"})
        assert response.status_code == 200
        assert response.json()["tenant_id"] is None

    def test_a_malformed_claim_is_a_401(self):
        response = self._client(tenant_id_claim="tid").get(
            "/t", headers={"Authorization": f"Bearer {self._token(tid=['acme'])}"}
        )
        assert response.status_code == 401
        assert "tenant" in response.json()["detail"].lower()
