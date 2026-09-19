"""#305 -- the real router backing ``/configure/sources`` (``InternalSource``).

``api/openapi/paths/configure.yaml`` defined this resource -- ``listSources``,
``createSource``, ``getSource``, ``updateSource``, ``deleteSource``, ``testSource`` -- before
#304 landed: a stub-only contract the R3 stub server has served examples from since phase
zero, with nothing behind it (the same "contract first, wiring later" shape
``semantic.py``'s router docstring describes). This module is that wiring: every operation
backed by #304's ``DataSourceStore``/``SecretStore`` for real, closing the "Create data
source" flow's actual gap (#302) -- today ``ConfigureInternalSourceForm.jsx`` writes only to
Supabase/localStorage, and this is the backend it should have been calling.

**Scope: ``InternalSource`` only.** ``ExternalIntegration`` (``/configure/integrations`` --
``ConfigureExternalForm.jsx``'s OAuth API connectors) is a different integration shape
entirely and is not touched here; see ``contracts/data_source.py``'s module docstring.

**Test-connection is a real, bounded probe, not a simulation.** ``POST
/configure/sources/{id}/test`` opens an actual connection with the stored credential,
under a short timeout, and closes it immediately -- replacing
``ConfigureInternalSourceForm.jsx``'s ``window.alert("Demo only...")`` stub. Two things this
deliberately does *not* do, both because this app now makes outbound connections to a
caller-supplied host and port, a new attack surface: it refuses to connect to a loopback or
link-local address (``_refused_host_reason`` -- link-local is where a cloud metadata
endpoint lives, e.g. AWS/Azure's ``169.254.169.254``, the single highest-value SSRF target;
loopback would let a caller probe the API process's own host), checked against the
*resolved* address, not the literal string, so a hostname cannot rebind past the check; and
it never returns a raw driver exception to the caller (``_classify_connection_error``) --
only a small, fixed vocabulary of reasons, so a stack trace or an internal hostname never
reaches the response. Only Postgres is actually implemented (:data:`_TESTABLE_SOURCE_TYPES`)
-- every other registered ``source_type`` has no driver in this product yet and says so
rather than pretending to have tried.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.settings import RetailSettings
from egpt_retail.configure.secrets import SecretStore, build_secret_store
from egpt_retail.configure.store import DataSourceStore
from egpt_retail.contracts.data_source import (
    AuthMethod,
    ConnectionTestResult,
    Credential,
    DataSource,
    DataSourceCreate,
    DataSourceType,
    DataSourceUpdate,
    DataSourceValidationError,
    PermissionLevel,
)

__all__ = ["router"]

router = APIRouter()


# --- connection + store dependencies -------------------------------------------------------


def _control_plane_connection() -> Iterator[Any]:
    """Shared by :func:`_source_store` and :func:`_secret_store` below -- FastAPI caches a
    ``Depends`` result per request, so a route asking for both gets one connection, not two."""
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        yield connection
    finally:
        connection.close()


def _source_store(
    connection: Any = Depends(_control_plane_connection),
) -> DataSourceStore:
    return DataSourceStore(connection)


def _secret_store(
    connection: Any = Depends(_control_plane_connection),
) -> SecretStore:
    return build_secret_store(RetailSettings(), connection)


def _caller_id(request: Request) -> str:
    """Mirrors ``kpis.py``'s ``_caller_id``/``dashboards.py``'s ``_owner`` exactly --
    duplicated, not imported, matching this codebase's existing convention of each router
    owning its own copy. Used only for ``created_by`` attribution, never as an authorization
    check -- ``require_scopes`` already gates every write."""
    if not getattr(request.state, "authorization_enabled", False):
        return "local"
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=401, detail="This token carries no subject claim."
        )
    return str(user_id)


def _not_found(source_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No such source: {source_id!r}.")


# --- wire conversions ------------------------------------------------------------------------


def _source_out(source: DataSource) -> wire.InternalSource:
    return wire.InternalSource(
        id=source.id,
        source_type=wire.SourceType(source.source_type.value),
        name=source.name,
        host=source.host,
        port=source.port,
        database_name=source.database_name,
        schema=source.schema_name,
        auth_method=wire.AuthMethod(source.auth_method.value),
        username=source.username,
        ssl_mode=source.ssl_mode,
        permission_level=wire.PermissionLevel(source.permission_level.value),
        has_credentials=True,
        last_harvest_id=source.last_harvest_run_id,
        created_at=source.created_at,
    )


def _credential_from_write(body: "wire.InternalSourceWrite") -> Credential:
    return Credential(
        auth_method=AuthMethod(body.auth_method.value),
        username=body.username,
        password=body.password,
        api_key=body.api_key,
    )


def _credential_from_patch(body: "wire.InternalSourcePatch") -> Optional[Credential]:
    """``None`` means "no credential in this patch, leave the stored one alone" -- distinct
    from a :class:`Credential` with every field empty, which would fail
    :func:`~egpt_retail.contracts.data_source.validate_credential` and correctly refuse the
    request rather than silently keeping the old secret. A patch rotates the credential only
    when it names an ``auth_method``; ``password``/``api_key`` alone with no ``auth_method``
    is ambiguous about which credential shape they belong to and is refused by
    :func:`validate_credential`."""
    if body.auth_method is None and body.password is None and body.api_key is None:
        return None
    auth_method = body.auth_method or AuthMethod.USERNAME_PASSWORD
    return Credential(
        auth_method=AuthMethod(auth_method.value),
        username=body.username,
        password=body.password,
        api_key=body.api_key,
    )


def _create_from_write(
    body: "wire.InternalSourceWrite", *, created_by: str
) -> DataSourceCreate:
    return DataSourceCreate(
        name=body.name,
        source_type=DataSourceType(body.source_type.value),
        host=body.host,
        database_name=body.database_name,
        credential=_credential_from_write(body),
        port=body.port,
        schema_name=body.schema_ or "",
        ssl_mode=body.ssl_mode or "require",
        permission_level=(
            PermissionLevel(body.permission_level.value)
            if body.permission_level is not None
            else PermissionLevel.READ_ONLY
        ),
        created_by=created_by,
    )


def _update_from_patch(body: "wire.InternalSourcePatch") -> DataSourceUpdate:
    return DataSourceUpdate(
        name=body.name,
        host=body.host,
        port=body.port,
        database_name=body.database_name,
        schema_name=body.schema_,
        ssl_mode=body.ssl_mode,
        permission_level=(
            PermissionLevel(body.permission_level.value)
            if body.permission_level is not None
            else None
        ),
        credential=_credential_from_patch(body),
    )


# --- routes ------------------------------------------------------------------------------


@router.get("/configure/sources", response_model=list[wire.InternalSource])
def list_sources(
    store: DataSourceStore = Depends(_source_store),
    _: None = Depends(require_scopes("config:read")),
):
    return [_source_out(source) for source in store.list()]


@router.post("/configure/sources", response_model=wire.InternalSource, status_code=201)
def create_source(
    body: "wire.InternalSourceWrite",
    request: Request,
    store: DataSourceStore = Depends(_source_store),
    secrets: SecretStore = Depends(_secret_store),
    _: None = Depends(require_scopes("configure:write")),
):
    created_by = _caller_id(request)
    try:
        created = store.create(_create_from_write(body, created_by=created_by), secrets)
    except DataSourceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _source_out(created)


@router.get("/configure/sources/{source_id}", response_model=wire.InternalSource)
def get_source(
    source_id: str,
    store: DataSourceStore = Depends(_source_store),
    _: None = Depends(require_scopes("config:read")),
):
    source = store.get(source_id)
    if source is None:
        raise _not_found(source_id)
    return _source_out(source)


@router.patch("/configure/sources/{source_id}", response_model=wire.InternalSource)
def update_source(
    source_id: str,
    body: "wire.InternalSourcePatch",
    store: DataSourceStore = Depends(_source_store),
    secrets: SecretStore = Depends(_secret_store),
    _: None = Depends(require_scopes("configure:write")),
):
    try:
        updated = store.update(source_id, _update_from_patch(body), secrets)
    except DataSourceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise _not_found(source_id)
    return _source_out(updated)


@router.delete("/configure/sources/{source_id}", status_code=204)
def delete_source(
    source_id: str,
    store: DataSourceStore = Depends(_source_store),
    secrets: SecretStore = Depends(_secret_store),
    _: None = Depends(require_scopes("configure:write")),
):
    if not store.delete(source_id, secrets):
        raise _not_found(source_id)
    return None


# --- test-connection: SSRF guard, driver dispatch, safe error classification --------------

#: Source types with an actual driver in this product today. Every other registered
#: ``source_type`` returns a clear "not implemented" result rather than pretending to have
#: tried -- the same honesty principle #306's ``HARVESTABLE_SOURCE_TYPES`` already applies to
#: harvest (that set also names Snowflake, which does not yet have a driver here either --
#: #306 is where that dependency will actually be added; this set is narrower on purpose,
#: scoped to what *this* router can attempt today).
_TESTABLE_SOURCE_TYPES = frozenset({DataSourceType.POSTGRES})

_CONNECT_TIMEOUT_SECONDS = 5.0


def _refused_host_reason(host: str) -> Optional[str]:
    """Refuses loopback and link-local addresses, checked against the *resolved* address
    (not the literal ``host`` string), so ``metadata.internal`` or any other hostname that
    resolves to ``169.254.169.254`` is caught the same as the literal IP would be. Private
    (RFC1918) addresses are deliberately allowed -- that is where a real customer's own
    warehouse legitimately lives, and refusing it would break the primary use case this
    router exists for. Returns ``None`` when the host is fine to try."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return (
            None  # DNS failure surfaces as "unreachable" once the real connect is tried
        )
    for info in infos:
        addr = ip_address(info[4][0])
        if addr.is_loopback or addr.is_link_local:
            return (
                "refused: this host resolves to a loopback or link-local address, which "
                "this deployment does not connect to."
            )
    return None


def _classify_connection_error(exc: BaseException) -> str:
    """A small, fixed vocabulary -- never the raw driver exception. See the module
    docstring's security note."""
    text = str(exc).lower()
    if (
        "password authentication failed" in text
        or "role" in text
        and "does not exist" in text
    ):
        return "Authentication failed."
    if "timeout" in text or "timed out" in text:
        return "Connection timed out."
    if "does not exist" in text and "database" in text:
        return "The named database does not exist."
    return "Could not connect."


def _test_postgres_connection(
    source: DataSource, credential: Credential
) -> ConnectionTestResult:
    import psycopg

    now = datetime.now(timezone.utc)
    try:
        connection = psycopg.connect(
            host=source.host,
            port=source.port or 5432,
            dbname=source.database_name,
            user=credential.username,
            password=credential.password or credential.api_key,
            connect_timeout=int(_CONNECT_TIMEOUT_SECONDS),
            sslmode=source.ssl_mode or "require",
        )
    except Exception as exc:  # psycopg raises a wide OperationalError family here
        return ConnectionTestResult(
            ok=False, message=_classify_connection_error(exc), tested_at=now
        )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
                (source.schema_name or "public",),
            )
            row = cursor.fetchone()
            table_count = row[0] if row is not None else 0
        message = (
            f"Connected as {credential.username}; {table_count} table(s) visible in "
            f"schema {source.schema_name or 'public'}."
        )
        return ConnectionTestResult(ok=True, message=message, tested_at=now)
    finally:
        connection.close()


@router.post("/configure/sources/{source_id}/test")
def test_source(
    source_id: str,
    store: DataSourceStore = Depends(_source_store),
    secrets: SecretStore = Depends(_secret_store),
    _: None = Depends(require_scopes("configure:write")),
):
    source = store.get(source_id)
    if source is None:
        raise _not_found(source_id)

    now = datetime.now(timezone.utc)
    refusal = _refused_host_reason(source.host)
    if refusal is not None:
        result = ConnectionTestResult(ok=False, message=refusal, tested_at=now)
    elif source.source_type not in _TESTABLE_SOURCE_TYPES:
        result = ConnectionTestResult(
            ok=False,
            message=f"Testing a {source.source_type.value} connection is not implemented yet.",
            tested_at=now,
        )
    else:
        credential = secrets.read(source.secret_ref)
        result = _test_postgres_connection(source, credential)

    store.record_test_result(source_id, result)
    return {"ok": result.ok, "detail": result.message, "tested_at": result.tested_at}
