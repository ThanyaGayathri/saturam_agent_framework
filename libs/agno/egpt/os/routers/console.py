"""The built-in admin console (EGPT-2.9).

A single self-contained HTML page served at ``GET /console``, backed entirely by the
``/components`` authoring API. It ships as package data rather than as a built asset: no build
step, no CDN, no node toolchain in this repo.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# The page loads nothing external, which is what makes this policy enforceable -- and the policy
# is what keeps a future edit from quietly reintroducing a CDN. The operator's bearer token lives
# in this origin's sessionStorage, so "no third-party script" is a security property here, not
# just an air-gap convenience. 'unsafe-inline' is unavoidable for a single-file page.
#
# ``base-uri`` is listed explicitly because ``default-src`` does **not** cover it -- absent its own
# directive it defaults permissive. With ``script-src 'unsafe-inline'`` unavoidable for a
# single-file page, an injected ``<base href>`` would repoint every relative fetch this page makes,
# including the ones carrying the operator's bearer token. There is no injection sink today (the
# page renders every server-supplied value with ``textContent``), so this closes a step in a chain
# rather than a live hole.
CONSOLE_CSP = (
    "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


def console_asset_path() -> Path:
    """The console page that ships with the package.

    Resolved through ``importlib.resources`` rather than ``__file__`` so it works the same from a
    checkout and from an installed wheel -- the whole reason the page is package data. See the
    ``[tool.setuptools.package-data]`` entry in ``libs/agno/pyproject.toml``.
    """
    from importlib.resources import files

    return Path(str(files("egpt.os") / "static" / "console" / "index.html"))


def get_console_router() -> APIRouter:
    """Serve the admin console at ``GET /console``.

    An explicit route returning ``FileResponse``, deliberately not ``StaticFiles(html=True)``:
    ``TrailingSlashMiddleware`` strips the trailing slash that ``StaticFiles`` relies on for
    index resolution, which produces a redirect loop (the same trap documented in
    ``cookbook/data_labeling/image_search/run.py``).

    The route is *unauthenticated*, and ``EgptOS`` adds ``/console`` to the auth middleware's
    excluded paths when the console is enabled. This is not an oversight: a browser navigating
    here carries no ``Authorization`` header, and the page is an inert asset with no tenant data
    in it. Every call it then makes goes through the gated ``/components`` routes with a token
    the operator pastes in, so scopes and tenancy are enforced exactly as for any API client.
    For the same reason there is deliberately no ``os/scopes.py`` entry for this path.
    """
    router = APIRouter(tags=["Console"])

    # include_in_schema=False: an HTML page is not part of the API contract, and an OpenAPI
    # client generated from this document should not grow a `get_console` method.
    @router.get("/console", include_in_schema=False)
    async def serve_console() -> FileResponse:
        return FileResponse(
            console_asset_path(),
            media_type="text/html",
            headers={
                # no-store, so upgrading the package cannot leave a browser on the old console
                # talking to a new API.
                "Cache-Control": "no-store",
                "Content-Security-Policy": CONSOLE_CSP,
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    return router
