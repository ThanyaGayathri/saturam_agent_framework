"""Entra-shaped scopes-claim expansion and config plumbing (AZ-D / gap #1).

`AuthorizationConfig` gets a `scopes_claim` field threaded through
`build_jwt_middleware_kwargs` to the `AuthMiddleware`/`JWTValidator` constructors, plus a
`scopes_claim_format` field: Entra emits `roles` (a JSON list) or `scp` (a single
space-delimited string), neither of which is the framework's plain `scopes` list. This file
follows the same shape as `test_tenant_claim.py` (`tenant_id_claim` is the precedent field/
pattern) -- claim extraction, config plumbing, and an end-to-end pass through the middleware.

The load-bearing regression check is `TestDefaultFormatIsUnchanged`: every existing caller
using the default `scopes` claim and the default `"list"` format must see the exact same
behaviour as before this change -- a bare string still becomes a one-element list, a
malformed shape still resolves to `[]`.
"""

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt.os.config import AuthorizationConfig
from egpt.os.middleware import JWTMiddleware
from egpt.os.middleware.jwt import JWTValidator, build_jwt_middleware_kwargs, coerce_scopes_claim

JWT_SECRET = "test-secret-key-for-scopes-claim-tests"


# =============================================================================
# coerce_scopes_claim -- the shared coercion function itself
# =============================================================================


class TestDefaultFormatIsUnchanged:
    """Regression: the default ("list") format must behave exactly as the old inline
    coercion did in both JWTValidator.extract_claims and AuthMiddleware.dispatch, whether or
    not a caller ever heard of scopes_claim_format."""

    def test_a_list_claim_passes_through(self):
        assert coerce_scopes_claim(["agents:read", "agents:run"]) == ["agents:read", "agents:run"]

    def test_a_bare_string_becomes_a_one_element_list(self):
        assert coerce_scopes_claim("agents:read") == ["agents:read"]

    def test_a_missing_claim_default_of_empty_list_stays_empty(self):
        assert coerce_scopes_claim([]) == []

    @pytest.mark.parametrize("value", [42, None, {"scope": "x"}, True, 3.14])
    def test_a_malformed_shape_refuses_cleanly_to_empty_list(self, value):
        assert coerce_scopes_claim(value) == []

    def test_explicitly_passing_the_list_format_matches_the_default(self):
        assert coerce_scopes_claim("agents:read", "list") == ["agents:read"]
        assert coerce_scopes_claim(["a", "b"], "list") == ["a", "b"]


class TestSpaceDelimitedFormat:
    """Entra's `scp` claim: a single space-delimited string of scope names."""

    def test_a_scp_shaped_string_splits_into_real_scopes(self):
        assert coerce_scopes_claim("agents.read agents.run", "space_delimited") == [
            "agents.read",
            "agents.run",
        ]

    def test_a_list_claim_is_never_split_even_under_space_delimited(self):
        """Entra's `roles` claim is already a list -- space_delimited must not touch it."""
        assert coerce_scopes_claim(["agents:read", "agents:run"], "space_delimited") == [
            "agents:read",
            "agents:run",
        ]

    def test_a_single_scope_string_still_becomes_a_one_element_list(self):
        assert coerce_scopes_claim("agents.read", "space_delimited") == ["agents.read"]

    @pytest.mark.parametrize("value", [42, None, {"scope": "x"}, True])
    def test_a_malformed_shape_still_refuses_cleanly_under_space_delimited(self, value):
        assert coerce_scopes_claim(value, "space_delimited") == []

    def test_extra_whitespace_is_collapsed(self):
        assert coerce_scopes_claim("  agents.read   agents.run  ", "space_delimited") == [
            "agents.read",
            "agents.run",
        ]


# =============================================================================
# JWTValidator.extract_claims -- one of the two duplicated coercion sites
# =============================================================================


class TestExtractClaims:
    def test_default_scopes_claim_and_format(self):
        validator = JWTValidator(verification_keys=[JWT_SECRET], algorithm="HS256")
        claims = validator.extract_claims({"sub": "u", "scopes": "agents:read"})
        assert claims["scopes"] == ["agents:read"]

    def test_scp_claim_with_space_delimited_format(self):
        validator = JWTValidator(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            scopes_claim="scp",
            scopes_claim_format="space_delimited",
        )
        claims = validator.extract_claims({"sub": "u", "scp": "agents.read agents.run"})
        assert claims["scopes"] == ["agents.read", "agents.run"]

    def test_roles_claim_as_a_list(self):
        validator = JWTValidator(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            scopes_claim="roles",
        )
        claims = validator.extract_claims({"sub": "u", "roles": ["agents:read", "agents:run"]})
        assert claims["scopes"] == ["agents:read", "agents:run"]

    def test_a_malformed_configured_claim_still_refuses_to_empty_list(self):
        validator = JWTValidator(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            scopes_claim="scp",
            scopes_claim_format="space_delimited",
        )
        claims = validator.extract_claims({"sub": "u", "scp": 42})
        assert claims["scopes"] == []


# =============================================================================
# Config plumbing -- AuthorizationConfig -> build_jwt_middleware_kwargs
# =============================================================================


class TestMiddlewareKwargs:
    def test_scopes_claim_is_forwarded_when_configured(self):
        kwargs = build_jwt_middleware_kwargs(AuthorizationConfig(scopes_claim="scp"), authorization=True)
        assert kwargs["scopes_claim"] == "scp"

    def test_scopes_claim_is_omitted_when_unset(self):
        assert "scopes_claim" not in build_jwt_middleware_kwargs(AuthorizationConfig(), authorization=True)

    def test_no_authorization_config_at_all_omits_scopes_claim(self):
        assert "scopes_claim" not in build_jwt_middleware_kwargs(None, authorization=True)

    def test_the_default_scopes_claim_is_none(self):
        """None means 'use the framework default of scopes' -- AuthorizationConfig itself
        carries no opinion, matching tenant_id_claim's precedent."""
        assert AuthorizationConfig().scopes_claim is None

    def test_scopes_claim_format_is_forwarded_when_non_default(self):
        kwargs = build_jwt_middleware_kwargs(
            AuthorizationConfig(scopes_claim="scp", scopes_claim_format="space_delimited"),
            authorization=True,
        )
        assert kwargs["scopes_claim_format"] == "space_delimited"

    def test_scopes_claim_format_is_omitted_at_its_default(self):
        """The default ("list") is never forwarded, so a manual app.add_middleware(...) setup's
        own default keeps applying -- same convention as tenant_id_claim/admin_scope."""
        kwargs = build_jwt_middleware_kwargs(AuthorizationConfig(scopes_claim="scp"), authorization=True)
        assert "scopes_claim_format" not in kwargs

    def test_the_default_scopes_claim_format_is_list(self):
        assert AuthorizationConfig().scopes_claim_format == "list"


# =============================================================================
# End to end through the middleware -- an integration-shaped test, not just
# a unit test of the coercion function in isolation.
# =============================================================================


class TestThroughTheMiddleware:
    def _client(self, **middleware_kwargs):
        app = FastAPI()

        @app.get("/t")
        async def read(request: Request):
            return {"scopes": request.state.scopes}

        app.add_middleware(JWTMiddleware, verification_keys=[JWT_SECRET], algorithm="HS256", **middleware_kwargs)
        return TestClient(app)

    def _token(self, **claims):
        return jwt.encode({"sub": "user-1", **claims}, JWT_SECRET, algorithm="HS256")

    def test_the_default_scopes_claim_behaves_exactly_as_before(self):
        """No scopes_claim/scopes_claim_format configured at all -- proves the OLD behaviour
        (string -> one-element list) is unchanged when the new fields are left at default."""
        response = self._client().get("/t", headers={"Authorization": f"Bearer {self._token(scopes='agents:read')}"})
        assert response.status_code == 200
        assert response.json()["scopes"] == ["agents:read"]

    def test_a_custom_scopes_claim_reaches_the_middleware_from_authorization_config(self):
        """The full path: AuthorizationConfig -> build_jwt_middleware_kwargs -> the constructed
        middleware's actual claim-name behaviour."""
        kwargs = build_jwt_middleware_kwargs(
            AuthorizationConfig(scopes_claim="scp", scopes_claim_format="space_delimited"),
            authorization=False,
        )
        client = self._client(
            scopes_claim=kwargs["scopes_claim"],
            scopes_claim_format=kwargs["scopes_claim_format"],
        )
        response = client.get("/t", headers={"Authorization": f"Bearer {self._token(scp='agents.read agents.run')}"})
        assert response.status_code == 200
        assert response.json()["scopes"] == ["agents.read", "agents.run"]

    def test_a_roles_claim_list_reaches_the_middleware(self):
        kwargs = build_jwt_middleware_kwargs(AuthorizationConfig(scopes_claim="roles"), authorization=False)
        client = self._client(scopes_claim=kwargs["scopes_claim"])
        response = client.get(
            "/t", headers={"Authorization": f"Bearer {self._token(roles=['agents:read', 'agents:run'])}"}
        )
        assert response.status_code == 200
        assert response.json()["scopes"] == ["agents:read", "agents:run"]

    def test_a_malformed_scopes_claim_does_not_crash_and_resolves_to_no_scopes(self):
        kwargs = build_jwt_middleware_kwargs(
            AuthorizationConfig(scopes_claim="scp", scopes_claim_format="space_delimited"),
            authorization=False,
        )
        client = self._client(scopes_claim=kwargs["scopes_claim"], scopes_claim_format=kwargs["scopes_claim_format"])
        response = client.get("/t", headers={"Authorization": f"Bearer {self._token(scp=42)}"})
        assert response.status_code == 200
        assert response.json()["scopes"] == []

    def test_an_unconfigured_deployment_is_unaffected(self):
        """No AuthorizationConfig at all -- build_jwt_middleware_kwargs omits both new keys, so
        a manually constructed middleware keeps its own defaults."""
        kwargs = build_jwt_middleware_kwargs(None, authorization=False)
        assert "scopes_claim" not in kwargs
        assert "scopes_claim_format" not in kwargs
        client = self._client()
        response = client.get("/t", headers={"Authorization": f"Bearer {self._token(scopes=['agents:read'])}"})
        assert response.status_code == 200
        assert response.json()["scopes"] == ["agents:read"]
