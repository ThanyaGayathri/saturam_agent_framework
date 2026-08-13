"""Wiring of the built-in admin console (EGPT-2.9).

The console is an inert HTML asset, so almost nothing about it is interesting -- except *where*
it is wired, which has four traps this file exists to pin:

1. It is opt-in. A deployment that did not ask for it must not grow a `/console` route.
2. It is registered in `_add_built_in_routes`, not alongside the `/components` router it drives.
   `_reprovision_routers` clears `app.router.routes` and then calls `_add_built_in_routes`, so a
   `/console` added anywhere else survives `get_app()` and silently vanishes on the first
   `resync()` -- green in a test that only builds the app, broken in a deployment that resyncs.
3. It is excluded from the auth middleware. A browser navigating here sends no `Authorization`
   header, so without the exclusion the page 401s before it can ask for a token. That exclusion
   is done by *replacing* the middleware's defaults, so the defaults have to come along.
4. It ships as package data. Without the `pyproject.toml` entry it works from a checkout and
   500s from a wheel, which no other test in the suite would notice.
"""

import pytest
from fastapi.testclient import TestClient

from egpt.integrations.db.sqlite import SqliteDb
from egpt.os.app import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.middleware.jwt import DEFAULT_EXCLUDED_ROUTE_PATHS
from egpt.os.routers.console import console_asset_path

SECURITY_KEY = "test-security-key-for-console"


def build(tmp_path, name, **kwargs):
    db = SqliteDb(db_file=str(tmp_path / f"{name}.db"))
    return db, EgptOS(db=db, id="console-test", **kwargs).get_app()


def get_console(app, headers=None):
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        return client.get("/console", headers=headers or {})


# =============================================================================
# Opt-in
# =============================================================================


class TestTheConsoleIsOptIn:
    def test_it_is_absent_by_default(self, tmp_path):
        _, app = build(tmp_path, "default")

        assert get_console(app).status_code == 404

    def test_it_is_served_when_enabled(self, tmp_path):
        _, app = build(tmp_path, "enabled", enable_console=True)

        response = get_console(app)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_it_is_not_in_the_openapi_document(self, tmp_path):
        """An HTML page is not part of the API contract; a generated client must not grow it."""
        _, app = build(tmp_path, "openapi", enable_console=True)

        assert "/console" not in app.openapi()["paths"]

    def test_it_sends_a_content_security_policy(self, tmp_path):
        """Enforceable only because the page loads nothing external. See routers/console.py."""
        _, app = build(tmp_path, "csp", enable_console=True)

        response = get_console(app)

        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"


# =============================================================================
# The page and the API agree
# =============================================================================


class TestThePageTargetsTheRealApi:
    """A console pointing at a renamed endpoint should break here, loudly, not in a browser."""

    @pytest.fixture
    def page(self, tmp_path):
        _, app = build(tmp_path, "page", enable_console=True)
        return get_console(app).text

    @pytest.mark.parametrize(
        "path",
        [
            "/components",
            "/components/validate",
            "/configs",
            "/set-current",
            "/audit",
        ],
    )
    def test_it_calls(self, page, path):
        assert path in page

    def test_it_loads_nothing_external(self, page):
        """The operator's token lives in this origin's sessionStorage. No third-party script may
        be in a position to read it -- and the CSP above would break the page if one were."""
        for marker in ("http://", "https://", "//cdn", "<link", "<img"):
            assert marker not in page, f"the console must not reference {marker!r}"


# =============================================================================
# Auth exclusion
# =============================================================================


class TestTheConsolePageIsReachableWithoutACredential:
    """The page is inert. The API it calls is not, and stays gated in every case below."""

    def test_under_a_security_key_deployment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EGPT_OS_SECURITY_KEY", SECURITY_KEY)
        _, app = build(tmp_path, "seckey", enable_console=True)

        assert get_console(app).status_code == 200

        client = TestClient(app, raise_server_exceptions=False)
        with client:
            assert client.get("/components").status_code == 401

    def test_under_a_tenant_claim_deployment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EGPT_OS_SECURITY_KEY", SECURITY_KEY)
        _, app = build(
            tmp_path,
            "claim",
            enable_console=True,
            authorization_config=AuthorizationConfig(tenant_id_claim="tid"),
        )

        assert get_console(app).status_code == 200

        client = TestClient(app, raise_server_exceptions=False)
        with client:
            assert client.get("/components").status_code == 401

    def test_a_disabled_console_adds_no_exclusion(self, tmp_path, monkeypatch):
        """The exclusion is a consequence of the flag, not a permanent hole in the auth layer."""
        monkeypatch.setenv("EGPT_OS_SECURITY_KEY", SECURITY_KEY)
        _, app = build(tmp_path, "noconsole", authorization_config=AuthorizationConfig(tenant_id_claim="tid"))

        for middleware in app.user_middleware:
            excluded = middleware.kwargs.get("excluded_route_paths") if middleware.kwargs else None
            if excluded:
                assert "/console" not in excluded


class TestTheMiddlewareDefaultsSurviveTheAddition:
    """Passing `excluded_route_paths` REPLACES the middleware defaults. Losing one of them would
    silently start requiring a credential for /docs or /health, which no console test would
    otherwise catch."""

    def _excluded(self, app):
        for middleware in app.user_middleware:
            if middleware.kwargs and "excluded_route_paths" in middleware.kwargs:
                return middleware.kwargs["excluded_route_paths"]
        return None

    def test_enabling_the_console_keeps_every_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EGPT_OS_SECURITY_KEY", SECURITY_KEY)
        _, app = build(
            tmp_path,
            "defaults",
            enable_console=True,
            authorization_config=AuthorizationConfig(tenant_id_claim="tid"),
        )

        excluded = self._excluded(app)

        assert excluded is not None
        for default in DEFAULT_EXCLUDED_ROUTE_PATHS:
            assert default in excluded
        assert "/console" in excluded

    def test_the_health_and_docs_routes_stay_open(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EGPT_OS_SECURITY_KEY", SECURITY_KEY)
        _, app = build(
            tmp_path,
            "open",
            enable_console=True,
            authorization_config=AuthorizationConfig(tenant_id_claim="tid"),
        )

        client = TestClient(app, raise_server_exceptions=False)
        with client:
            assert client.get("/health").status_code == 200
            assert client.get("/openapi.json").status_code == 200

    def test_no_console_and_no_interfaces_leaves_the_kwarg_none(self, tmp_path, monkeypatch):
        """None is what lets the middleware keep owning its own defaults. An empty list would not."""
        monkeypatch.setenv("EGPT_OS_SECURITY_KEY", SECURITY_KEY)
        _, app = build(tmp_path, "none", authorization_config=AuthorizationConfig(tenant_id_claim="tid"))

        assert self._excluded(app) is None


# =============================================================================
# Survives the things that rebuild routes
# =============================================================================


class TestTheRouteSurvives:
    def test_a_resync(self, tmp_path):
        """`_reprovision_routers` clears app.router.routes. This is the trap the placement in
        `_add_built_in_routes` exists to avoid."""
        db = SqliteDb(db_file=str(tmp_path / "resync.db"))
        os_instance = EgptOS(db=db, id="console-resync", enable_console=True)
        app = os_instance.get_app()

        os_instance.resync(app)

        assert get_console(app).status_code == 200

    def test_the_root_mcp_mount(self, tmp_path):
        """MCP mounts at "/" and would swallow everything below it, but built-in routes are
        registered first in both assembly paths and Starlette matches in registration order.

        Asserted by serving the page rather than by inspecting `app.routes`: with MCP enabled the
        routers are lazy `_IncludedRouter` wrappers whose `.path` is None, so a route-list probe
        would pass here for the wrong reason.
        """
        pytest.importorskip("fastmcp", reason="MCP server requires the optional mcp extra")
        _, app = build(tmp_path, "mcp", enable_console=True, enable_mcp_server=True)

        response = get_console(app)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


# =============================================================================
# Packaging
# =============================================================================


class TestTheAssetShips:
    def test_the_console_page_is_on_disk(self):
        """Guards the `[tool.setuptools.package-data]` entry in libs/agno/pyproject.toml. Without
        it the page is absent from the built wheel, and `enable_console=True` serves 500s to
        anyone who installed from PyPI rather than from a checkout."""
        path = console_asset_path()

        assert path.is_file(), f"{path} is missing; check the package-data entry in pyproject.toml"
        assert path.read_text(encoding="utf-8").lstrip().startswith("<!doctype html>")
