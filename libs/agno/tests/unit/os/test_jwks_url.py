"""JWKS URL support with refresh (AZ-D / gap #2).

``AuthorizationConfig.jwks_file`` gains a URL mode: a value starting with ``http://`` or
``https://`` is fetched and cached via PyJWT's own ``PyJWKClient`` instead of the plain
``open()``+``json.load()`` local-file path -- reusing a library primitive that already does
HTTP fetch + TTL cache + automatic re-fetch on an unrecognized ``kid``, so a rotated signing
key does not require a manual file re-mount and pod restart.

This is fully local: a real ``http.server`` in a background thread serves a real JWKS
document over HTTP, and a real RSA key pair signs tokens -- no mocks, no external Azure/Entra
dependency, per the migration plan's verification convention. The key generation reuses
``egpt_retail.app.devauth.generate_rsa_jwks`` (the same helper the local-dev JWKS-file flow
already uses), serving the JSON over HTTP instead of writing it to a file.

Key rotation: a live in-process rotation test would need to wait out PyJWKClient's TTL cache,
which is impractical here and is PyJWT's own tested responsibility, not this framework's. What
IS proven: a *fresh* JWTValidator instance picks up a rotated key immediately (the operational
reality after a config reload / new worker), and the fetch-and-validate path itself works end
to end against a real HTTP JWKS document.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Tuple

import jwt as pyjwt
import pytest

from egpt.os.config import AuthorizationConfig
from egpt.os.middleware.jwt import JWTValidator

egpt_retail_devauth = pytest.importorskip(
    "egpt_retail.app.devauth", reason="product/egpt_retail is a sibling distribution, not always installed"
)


class _JWKSHandler(BaseHTTPRequestHandler):
    """Serves whatever JWKS document is currently set on the server instance."""

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's own naming)
        body = json.dumps(self.server.jwks_document).encode("utf-8")  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # Silence per-request logging -- keeps test output readable.


class _JWKSServer:
    """A real local HTTP server serving a mutable JWKS document, for JWKS-URL tests."""

    def __init__(self, jwks_document: Dict[str, object]):
        self._httpd = HTTPServer(("127.0.0.1", 0), _JWKSHandler)
        self._httpd.jwks_document = jwks_document  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/jwks.json"

    def set_document(self, jwks_document: Dict[str, object]) -> None:
        self._httpd.jwks_document = jwks_document  # type: ignore[attr-defined]

    def stop(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)


def _mint(private_pem: str, kid: str, scopes: Tuple[str, ...] = ("agents:read",)) -> str:
    now = int(time.time())
    claims: Dict[str, object] = {"sub": "alice", "scopes": list(scopes), "iat": now, "exp": now + 3600}
    return pyjwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def jwks_server():
    private_pem, jwks = egpt_retail_devauth.generate_rsa_jwks(kid="key-1")
    server = _JWKSServer(jwks)
    server.private_pem = private_pem  # type: ignore[attr-defined]
    yield server
    server.stop()


class TestJwksUrlDetection:
    def test_an_http_url_is_detected(self):
        from egpt.os.middleware.jwt import _is_jwks_url

        assert _is_jwks_url("http://example.com/jwks.json") is True
        assert _is_jwks_url("https://example.com/jwks.json") is True

    def test_a_local_path_is_not_a_url(self):
        from egpt.os.middleware.jwt import _is_jwks_url

        assert _is_jwks_url("/etc/egpt/jwks.json") is False
        assert _is_jwks_url("jwks.json") is False
        assert _is_jwks_url("relative/path/jwks.json") is False


class TestExistingFilePathBehaviourIsUnchanged:
    """A genuine local file path must keep going through the original open()+json.load()
    path -- strictly additive, no PyJWKClient involved."""

    def test_a_local_jwks_file_still_loads_via_the_static_dict(self, tmp_path):
        _, jwks = egpt_retail_devauth.generate_rsa_jwks(kid="key-1")
        jwks_path = tmp_path / "jwks.json"
        jwks_path.write_text(json.dumps(jwks))

        validator = JWTValidator(jwks_file=str(jwks_path), algorithm="RS256")

        assert validator._jwks_client is None
        assert "key-1" in validator.jwks_keys

    def test_a_missing_local_file_still_raises_value_error(self):
        with pytest.raises(ValueError, match="JWKS file not found"):
            JWTValidator(jwks_file="/nonexistent/path/jwks.json", algorithm="RS256")


class TestJwksUrl:
    def test_construction_from_a_url_uses_pyjwkclient_not_the_static_dict(self, jwks_server):
        validator = JWTValidator(jwks_file=jwks_server.url, algorithm="RS256")
        assert validator._jwks_client is not None
        assert validator.jwks_keys == {}

    def test_a_token_signed_with_the_matching_key_validates_over_http(self, jwks_server):
        validator = JWTValidator(jwks_file=jwks_server.url, algorithm="RS256")
        token = _mint(jwks_server.private_pem, kid="key-1")

        payload = validator.validate_token(token)

        assert payload["sub"] == "alice"
        claims = validator.extract_claims(payload)
        assert claims["scopes"] == ["agents:read"]

    def test_a_token_signed_with_an_unknown_key_is_rejected(self, jwks_server):
        other_pem, _ = egpt_retail_devauth.generate_rsa_jwks(kid="key-unknown")
        validator = JWTValidator(jwks_file=jwks_server.url, algorithm="RS256")
        token = _mint(other_pem, kid="key-unknown")

        # PyJWKClient raises its own PyJWKClientError (not InvalidTokenError) when a kid can't
        # be resolved even after refresh -- both are jwt.PyJWTError, and either way the token
        # is rejected, never silently accepted.
        with pytest.raises(pyjwt.PyJWTError):
            validator.validate_token(token)

    def test_authorization_config_with_a_jwks_url_end_to_end(self, jwks_server):
        """AuthorizationConfig.jwks_file accepting a URL directly -- no second field needed."""
        config = AuthorizationConfig(jwks_file=jwks_server.url, algorithm="RS256")
        assert config.jwks_file == jwks_server.url

        validator = JWTValidator(jwks_file=config.jwks_file, algorithm=config.algorithm or "RS256")
        token = _mint(jwks_server.private_pem, kid="key-1")
        payload = validator.validate_token(token)
        assert payload["sub"] == "alice"


class TestJwksKeyRotation:
    """Key rotation: a *new* instance resolves against a rotated key immediately. An
    in-process live-refresh test is left to PyJWT's own PyJWKClient test suite -- its TTL
    cache makes an in-process rotation assertion either flaky (real sleep) or vacuous
    (mocking away the exact behaviour under test); this proves the operationally relevant
    path instead: point at the URL, rotate the key server-side, and a fresh validator (the
    shape of a config reload / new worker after a genuine key rotation) resolves correctly."""

    def test_a_second_key_served_after_rotation_resolves_on_a_new_instance(self, jwks_server):
        first_validator = JWTValidator(jwks_file=jwks_server.url, algorithm="RS256")
        first_token = _mint(jwks_server.private_pem, kid="key-1")
        assert first_validator.validate_token(first_token)["sub"] == "alice"

        # Rotate: a new key pair becomes the only key the server offers.
        new_pem, new_jwks = egpt_retail_devauth.generate_rsa_jwks(kid="key-2")
        jwks_server.set_document(new_jwks)

        second_validator = JWTValidator(jwks_file=jwks_server.url, algorithm="RS256")
        second_token = _mint(new_pem, kid="key-2")
        payload = second_validator.validate_token(second_token)
        assert payload["sub"] == "alice"

        # The old key is gone server-side; a fresh instance no longer accepts an old-kid token.
        with pytest.raises(pyjwt.PyJWTError):
            second_validator.validate_token(first_token)
