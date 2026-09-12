"""AZ-D: Entra scopes-claim expansion and JWKS URL, run end to end through the real middleware.

Closes AZ-D / gaps #1 and #2 (docs/plan/azure-migration-plan.md, the gap analysis at
`/Users/rasik/.claude/plans/shimmying-twirling-piglet.md`):

  #1 `AuthorizationConfig` had no `scopes_claim` field (the JWTValidator/AuthMiddleware
     constructors already accepted one, but nothing at the config layer could reach it), and
     no roles/scp expansion -- Entra emits `roles` (a JSON list of role names) or `scp` (a
     single space-delimited string of scope names), never a plain `scopes` list.
  #2 `AuthorizationConfig.jwks_file` only ever loaded a local file once at construction, with
     no refresh -- a rotated Entra signing key needed a manual re-mount and restart.

Nothing here talks to a real Azure/Entra tenant. Per the migration plan's verification
convention ("run against the real service ... not mocks"), this runs against a REAL local HTTP
server serving a REAL JWKS document (a real RSA key pair, via
`egpt_retail.app.devauth.generate_rsa_jwks` -- the same helper the local-dev JWKS-file flow
already uses) and a REAL `AuthMiddleware` instance decoding REAL RS256-signed tokens. The
Entra tenant is the only thing that's simulated; the JWT/JWKS/middleware code path is exactly
what production runs.

Three scenarios, each asserted against a hand-computed expected scope set:
  A. Entra's `scp` claim (a space-delimited string) -> scopes_claim_format="space_delimited"
  B. Entra's `roles` claim (already a list) -> unaffected by the format, never split
  C. A JWKS URL (gap #2) instead of a JWKS file, serving the same document over HTTP
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict

from egpt.os.config import AuthorizationConfig
from egpt.os.middleware import JWTMiddleware
from egpt.os.middleware.jwt import build_jwt_middleware_kwargs
from egpt_retail.app.devauth import generate_rsa_jwks, mint_token
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


class _JWKSHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(self.server.jwks_document).encode("utf-8")  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def _serve_jwks(jwks_document: Dict[str, object]) -> tuple[str, HTTPServer]:
    httpd = HTTPServer(("127.0.0.1", 0), _JWKSHandler)
    httpd.jwks_document = jwks_document  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[:2]
    return f"http://{host}:{port}/jwks.json", httpd


def _client_for(authorization_config: AuthorizationConfig) -> TestClient:
    """The full config path: AuthorizationConfig -> build_jwt_middleware_kwargs -> a real,
    constructed AuthMiddleware -- not a unit test of the coercion function in isolation."""
    kwargs = build_jwt_middleware_kwargs(authorization_config, authorization=False)

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(request: Request) -> Dict[str, object]:
        return {"user_id": request.state.user_id, "scopes": request.state.scopes}

    app.add_middleware(JWTMiddleware, **kwargs)
    return TestClient(app)


def main() -> None:
    private_pem, jwks_document = generate_rsa_jwks(kid="az-d-demo")
    jwks_url, httpd = _serve_jwks(jwks_document)
    print(f"Serving a real JWKS document over HTTP at {jwks_url}")

    try:
        # --- Scenario A: Entra's `scp` claim (space-delimited string) -------------------
        print("\n--- Scenario A: scp (space-delimited) ---")
        scp_config = AuthorizationConfig(
            jwks_file=jwks_url,
            algorithm="RS256",
            scopes_claim="scp",
            scopes_claim_format="space_delimited",
        )
        scp_token = mint_token(
            private_pem,
            scopes=["agents.read", "agents.run"],
            subject="alice",
            kid="az-d-demo",
            scopes_claim="scp",
            scopes_claim_format="space_delimited",
        )
        response = _client_for(scp_config).get(
            "/whoami", headers={"Authorization": f"Bearer {scp_token}"}
        )
        print(f"status={response.status_code} body={response.json()}")
        expected_scp_scopes = ["agents.read", "agents.run"]
        assert response.status_code == 200
        assert response.json()["scopes"] == expected_scp_scopes, response.json()
        print(
            f"PASS: resolved scopes {response.json()['scopes']} == expected {expected_scp_scopes}"
        )

        # --- Scenario B: Entra's `roles` claim (already a list) -------------------------
        print("\n--- Scenario B: roles (list, unaffected by format) ---")
        roles_config = AuthorizationConfig(
            jwks_file=jwks_url, algorithm="RS256", scopes_claim="roles"
        )
        roles_token = mint_token(
            private_pem,
            scopes=["Retail.Admin", "Retail.Viewer"],
            subject="bob",
            kid="az-d-demo",
            scopes_claim="roles",
            scopes_claim_format="list",
        )
        response = _client_for(roles_config).get(
            "/whoami", headers={"Authorization": f"Bearer {roles_token}"}
        )
        print(f"status={response.status_code} body={response.json()}")
        expected_roles_scopes = ["Retail.Admin", "Retail.Viewer"]
        assert response.status_code == 200
        assert response.json()["scopes"] == expected_roles_scopes, response.json()
        print(
            f"PASS: resolved scopes {response.json()['scopes']} == expected {expected_roles_scopes}"
        )

        # --- Scenario C: the default scopes claim, unchanged, still over the JWKS URL ---
        print(
            "\n--- Scenario C: default scopes claim, unchanged behaviour, over a JWKS URL ---"
        )
        default_config = AuthorizationConfig(jwks_file=jwks_url, algorithm="RS256")
        default_token = mint_token(
            private_pem, scopes=["agents:read"], subject="carol", kid="az-d-demo"
        )
        response = _client_for(default_config).get(
            "/whoami", headers={"Authorization": f"Bearer {default_token}"}
        )
        print(f"status={response.status_code} body={response.json()}")
        expected_default_scopes = ["agents:read"]
        assert response.status_code == 200
        assert response.json()["scopes"] == expected_default_scopes, response.json()
        print(
            f"PASS: resolved scopes {response.json()['scopes']} == expected {expected_default_scopes}"
        )

        print(
            "\nAll AZ-D scenarios passed: scopes_claim/scopes_claim_format expansion and the"
        )
        print(
            "JWKS URL path both resolve real RS256 tokens to the expected internal scopes,"
        )
        print("through the real AuthMiddleware, with zero mocks.")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
