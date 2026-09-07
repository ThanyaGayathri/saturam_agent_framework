"""A FastAPI application built from the bundled contract at startup.

Routes are mounted from the bundle's ``paths``, so adding an endpoint to the YAML needs no
Python edit -- the property that keeps the YAML the source of truth in practice, not just
in principle. Each operation serves its declared examples; ``?example=<name>`` (or an
``X-Example`` header) selects one, because the prototype has screens for both the empty
and the populated state and a stub that can only serve one shape cannot render half of
them.

Two things are real rather than served from examples:

* **The semantic reads** are answered by Lane K's :class:`StubSemanticRead` over the
  contract fixture, so the three join-path outcomes -- a path, ``path: null``, and 503 for
  an unloaded reader -- are live behaviour, not described behaviour.
* **Scope gating**, when a JWT secret is configured: every operation requires the scopes
  its ``x-egpt-scopes`` names, and ``include_proposed=true`` additionally requires
  ``semantic:read_proposed``. The framework has no config surface for a product-defined
  scope in phase zero, so the stub reads the claim itself; registering the scope in the
  framework's map is a phase-five cost recorded in the contract's changelog.

Where the response schema names a generated model, the route declares it as its
``response_model``, so the served example is validated against the model on every request
-- a third check that spec, models and examples agree.

FastAPI is used rather than a generic mock server because the prototype runs on Vite at
another origin (CORS is one line here), because this repository has no Node toolchain, and
because the same app can later ride inside the R1 image with no extra layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from egpt_retail.api import models as generated_models
from egpt_retail.api.spec import BUNDLE_PATH, load_bundle
from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH
from egpt_retail.contracts.semantic import SemanticReadError
from egpt_retail.contracts.semantic_stub import FixtureNotLoaded, StubSemanticRead

__all__ = ["create_app", "mint_token", "serve", "aserve", "READ_PROPOSED_SCOPE"]

#: The scope `include_proposed=true` needs over HTTP. Defined by the contract, enforced here.
READ_PROPOSED_SCOPE = "semantic:read_proposed"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


# --- tokens ----------------------------------------------------------------------------


def mint_token(
    secret: str,
    scopes: Sequence[str],
    subject: str = "stub-user",
    ttl_seconds: int = 3600,
) -> str:
    """An HS256 token for local verification. Pure construction; no async twin.

    The real deployment verifies RS256 against the identity provider's JWKS (R2). This
    exists so a cookbook or a test can produce a 200 and a 403 without an issuer service.
    """
    import jwt

    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + ttl_seconds,
        },
        secret,
        algorithm="HS256",
    )


def _scopes_from_request(request: Request, secret: str) -> set:
    import jwt

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"detail": "Missing bearer token.", "code": "unauthenticated"},
        )
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401,
            detail={"detail": f"Invalid token: {exc}", "code": "unauthenticated"},
        )
    raw = claims.get("scope", "")
    return set(raw.split()) if isinstance(raw, str) else set(raw or ())


def _forbid(required: Sequence[str]) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "detail": f"Insufficient permissions. Required scopes: {', '.join(required)}.",
            "code": "insufficient_scope",
        },
    )


# --- examples ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Example:
    status: int
    name: str
    value: Any


def _examples_for(operation: Mapping[str, Any]) -> List[_Example]:
    """Every named response example, 2xx first in declaration order, then the rest."""
    found: List[_Example] = []
    for code, response in operation.get("responses", {}).items():
        if not code.isdigit():
            continue
        media = (response.get("content") or {}).get("application/json") or {}
        for name, example in (media.get("examples") or {}).items():
            found.append(
                _Example(status=int(code), name=name, value=example.get("value"))
            )
    found.sort(key=lambda e: 0 if 200 <= e.status < 300 else 1)
    return found


def _no_content_status(operation: Mapping[str, Any]) -> Optional[int]:
    for code in operation.get("responses", {}):
        if code == "204":
            return 204
    return None


def _model_for(schema: Optional[Mapping[str, Any]]) -> Any:
    """The generated model a response schema names, or ``None`` for an inline shape."""
    if not schema:
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        return getattr(generated_models, ref.rsplit("/", 1)[-1], None)
    if schema.get("type") == "array":
        item = _model_for(schema.get("items"))
        return List[item] if item is not None else None  # type: ignore[valid-type]
    return None


def _success_schema(operation: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    for code, response in operation.get("responses", {}).items():
        if code.isdigit() and 200 <= int(code) < 300:
            media = (response.get("content") or {}).get("application/json") or {}
            return media.get("schema")
    return None


# --- the semantic reads, answered live --------------------------------------------------


def _semantic_reader(fixture_path: Optional[Path]) -> StubSemanticRead:
    if fixture_path is None:
        return StubSemanticRead()
    return StubSemanticRead.from_fixture(fixture_path)


def _snapshot_payload(reader: StubSemanticRead) -> Dict[str, Any]:
    snap = reader.snapshot()
    return {
        "version": snap.version,
        "harvested_at": snap.harvested_at,
        "source": snap.source,
    }


def _table_ref(ref: Any) -> Dict[str, Any]:
    return {"name": ref.name, "schema": ref.schema, "row_count": ref.row_count}


def _column(column: Any) -> Dict[str, Any]:
    return {
        "name": column.name,
        "data_type": column.data_type,
        "nullable": column.nullable,
        "is_key": column.is_key,
        "is_personal_data": column.is_personal_data,
        "description": column.description,
        "sampled_values": list(column.sampled_values),
        "status": column.status.value,
    }


def _table(table: Any) -> Dict[str, Any]:
    return {
        "ref": _table_ref(table.ref),
        "columns": [_column(c) for c in table.columns],
        "description": table.description,
        "concepts": list(table.concepts),
        "status": table.status.value,
    }


def _concept(concept: Any) -> Dict[str, Any]:
    return {
        "name": concept.name,
        "description": concept.description,
        "synonyms": list(concept.synonyms),
        "question_patterns": list(concept.question_patterns),
        "sql_filter": concept.sql_filter,
        "personal_data_fields": list(concept.personal_data_fields),
        "compliance_tags": list(concept.compliance_tags),
        "metrics": list(concept.metrics),
        "business_area": concept.business_area,
        "status": concept.status.value,
    }


def _join_path(path: Any) -> Dict[str, Any]:
    return {
        "from_table": path.from_table,
        "to_table": path.to_table,
        "edges": [
            {
                "left_table": e.left_table,
                "right_table": e.right_table,
                "on": e.on,
                "cardinality": e.cardinality,
            }
            for e in path.edges
        ],
        "hops": path.hops,
        "intermediate_tables": list(path.intermediate_tables),
    }


def _semantic_handlers(reader: StubSemanticRead) -> Dict[str, Callable[..., Any]]:
    """Live handlers keyed by operationId. Anything not listed is served from examples."""

    def snapshot() -> Dict[str, Any]:
        return _snapshot_payload(reader)

    def list_tables(include_proposed: bool) -> Dict[str, Any]:
        return {
            "tables": [
                _table_ref(t)
                for t in reader.list_tables(include_proposed=include_proposed)
            ],
            "snapshot": _snapshot_payload(reader),
        }

    def get_table(table_name: str, include_proposed: bool) -> Dict[str, Any]:
        table = reader.get_table(table_name, include_proposed=include_proposed)
        if table is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "detail": f"No table named '{table_name}' in the semantic layer.",
                    "code": "not_found",
                },
            )
        return _table(table)

    def list_concepts(include_proposed: bool) -> Dict[str, Any]:
        return {
            "concepts": list(reader.list_concepts(include_proposed=include_proposed)),
            "snapshot": _snapshot_payload(reader),
        }

    def get_concept(concept_name: str, include_proposed: bool) -> Dict[str, Any]:
        concept = reader.get_concept(concept_name, include_proposed=include_proposed)
        if concept is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "detail": f"No concept named '{concept_name}' in this deployment.",
                    "code": "not_found",
                },
            )
        return _concept(concept)

    def list_business_areas(include_proposed: bool) -> Dict[str, Any]:
        return {
            "business_areas": list(
                reader.list_business_areas(include_proposed=include_proposed)
            ),
            "snapshot": _snapshot_payload(reader),
        }

    def find_join_path(from_table: str, to_table: str) -> Dict[str, Any]:
        try:
            path = reader.find_join_path(from_table, to_table)
        except FixtureNotLoaded:
            raise
        except SemanticReadError as exc:
            # An unknown endpoint is a bad request, not "no path": "no path from a table I
            # have never heard of" would be a misleading way to report a typo.
            raise HTTPException(
                status_code=400, detail={"detail": str(exc), "code": "unknown_table"}
            )
        return {
            "path": _join_path(path) if path is not None else None,
            "snapshot": _snapshot_payload(reader),
        }

    return {
        "getSemanticSnapshot": snapshot,
        "listSemanticTables": list_tables,
        "getSemanticTable": get_table,
        "listSemanticConcepts": list_concepts,
        "getSemanticConcept": get_concept,
        "listSemanticBusinessAreas": list_business_areas,
        "findSemanticJoinPath": find_join_path,
    }


# --- the app ------------------------------------------------------------------------------


def _route_sort_key(route: str) -> Tuple[int, int, str]:
    """Literal segments before templated ones at the same depth, so ``/use-cases/suggestions``
    is matched before ``/use-cases/{use_case_id}``."""
    segments = route.strip("/").split("/")
    return (len(segments), sum(1 for s in segments if s.startswith("{")), route)


def create_app(
    bundle_path: Path = BUNDLE_PATH,
    fixture_path: Optional[Path] = CONTRACT_FIXTURE_PATH,
    jwt_secret: Optional[str] = None,
    cors_origins: Sequence[str] = ("*",),
) -> FastAPI:
    """Build the stub. Pure construction; no async twin.

    ``fixture_path=None`` builds an unloaded semantic reader, so every semantic read answers
    503 -- the "cannot answer" outcome, distinct from every empty result.
    ``jwt_secret=None`` disables scope gating entirely; set it to exercise 200 versus 403.
    """
    bundle = load_bundle(bundle_path)
    reader = _semantic_reader(fixture_path)
    live = _semantic_handlers(reader)

    app = FastAPI(
        title=bundle["info"]["title"] + " (stub)",
        version=bundle["info"]["version"],
        description="Phase-zero contract stub. Serves declared examples; semantic reads are live over the contract fixture.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.reader = reader
    app.state.bundle = bundle

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        body = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"detail": str(exc.detail), "code": "error"}
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(FixtureNotLoaded)
    async def _unavailable(request: Request, exc: FixtureNotLoaded) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "code": "semantic_unavailable"},
        )

    for route in sorted(bundle["paths"], key=_route_sort_key):
        item = bundle["paths"][route]
        for method, operation in item.items():
            if method not in _HTTP_METHODS:
                continue
            _mount(app, route, method, operation, live, jwt_secret)

    @app.get("/stub/operations", include_in_schema=False)
    async def _operations() -> Dict[str, Any]:
        """What the stub mounted, for a cookbook to compare against the bundle."""
        return {
            "operations": [
                {
                    "method": m.upper(),
                    "path": r,
                    "operationId": op.get("operationId"),
                    "live": op.get("operationId") in live,
                }
                for r, it in bundle["paths"].items()
                for m, op in it.items()
                if m in _HTTP_METHODS
            ]
        }

    return app


def _mount(
    app: FastAPI,
    route: str,
    method: str,
    operation: Mapping[str, Any],
    live: Mapping[str, Callable[..., Any]],
    jwt_secret: Optional[str],
) -> None:
    operation_id = operation.get("operationId") or f"{method}_{route}"
    required_scopes: List[str] = list(operation.get("x-egpt-scopes") or [])
    examples = _examples_for(operation)
    no_content = _no_content_status(operation)
    live_handler = live.get(operation_id)
    response_model = _model_for(_success_schema(operation))

    async def endpoint(request: Request) -> Any:
        if jwt_secret is not None and required_scopes:
            granted = _scopes_from_request(request, jwt_secret)
            if not set(required_scopes) <= granted:
                return _forbid(required_scopes)
            if (
                request.query_params.get("include_proposed", "false").lower() == "true"
                and READ_PROPOSED_SCOPE not in granted
            ):
                return _forbid([READ_PROPOSED_SCOPE])

        if live_handler is not None:
            return _call_live(live_handler, request)

        wanted = request.query_params.get("example") or request.headers.get("x-example")
        if wanted:
            for example in examples:
                if example.name == wanted:
                    return JSONResponse(
                        status_code=example.status, content=example.value
                    )
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": f"No example named '{wanted}' on {operation_id}. Available: {', '.join(e.name for e in examples)}.",
                    "code": "unknown_example",
                },
            )
        success = [e for e in examples if 200 <= e.status < 300]
        if success:
            return JSONResponse(status_code=success[0].status, content=success[0].value)
        if no_content is not None:
            return Response(status_code=no_content)
        if examples:
            return JSONResponse(
                status_code=examples[0].status, content=examples[0].value
            )
        raise HTTPException(
            status_code=501,
            detail={
                "detail": f"{operation_id} declares no example.",
                "code": "no_example",
            },
        )

    endpoint.__name__ = operation_id
    app.add_api_route(
        route,
        endpoint,
        methods=[method.upper()],
        name=operation_id,
        summary=operation.get("summary"),
        description=operation.get("description"),
        tags=list(operation.get("tags") or []),
        response_model=response_model if live_handler is not None else None,
    )


def _call_live(handler: Callable[..., Any], request: Request) -> Any:
    """Bind path and query parameters to the live handler by name."""
    import inspect

    kwargs: Dict[str, Any] = {}
    params = inspect.signature(handler).parameters
    for name in params:
        if name in request.path_params:
            kwargs[name] = request.path_params[name]
        elif name == "include_proposed":
            kwargs[name] = (
                request.query_params.get("include_proposed", "false").lower() == "true"
            )
        elif name == "from_table":
            kwargs[name] = request.query_params.get("from", "")
        elif name == "to_table":
            kwargs[name] = request.query_params.get("to", "")
    if "from_table" in params and (
        not kwargs.get("from_table") or not kwargs.get("to_table")
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": "Query parameters 'from' and 'to' are required.",
                "code": "missing_parameter",
            },
        )
    return handler(**kwargs)


def serve(
    host: str = "127.0.0.1",
    port: int = 7778,
    jwt_secret: Optional[str] = None,
    load_fixture: bool = True,
) -> None:
    """Run the stub under uvicorn. Blocks until interrupted."""
    import uvicorn

    app = create_app(
        jwt_secret=jwt_secret,
        fixture_path=CONTRACT_FIXTURE_PATH if load_fixture else None,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


async def aserve(
    host: str = "127.0.0.1",
    port: int = 7778,
    jwt_secret: Optional[str] = None,
    load_fixture: bool = True,
) -> None:
    """Async variant: run the stub inside an existing event loop."""
    import uvicorn

    app = create_app(
        jwt_secret=jwt_secret,
        fixture_path=CONTRACT_FIXTURE_PATH if load_fixture else None,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()
