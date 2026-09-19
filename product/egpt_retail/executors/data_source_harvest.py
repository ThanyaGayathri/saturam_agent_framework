"""``harvest_source`` (#306): the ``harvest-source`` workflow's one step -- a registered
data source's own schema, read for real and written into the shared semantic graph.

The rest of #302's plan is what makes this exist: #304 built the place a source is
registered and its credential stored; #305 built the API that registers one and tests the
connection interactively. Neither makes a source's tables reachable by a KPI or the SQL
guard -- that is what writing a harvest into the graph does, and until this executor, nothing
did it for anything but the one operator-configured warehouse (``semantic/refresh.py``).

**Triggered the way ``docs/plan/build-plan.md``'s "Pipeline mechanism" already decided**:
``POST /workflows/harvest-source/runs`` (the framework's own endpoint,
``libs/agno/egpt/os/routers/workflows/router.py`` -- already reachable, nothing new to
build there) with ``additional_data={"source_id": "..."}``. No retail-specific "trigger a
harvest" endpoint exists or is needed; the scheduler's own callback already calls a
workflow's run endpoint over real HTTP for exactly this reason (``egpt.scheduler.executor.
ScheduleExecutor``), so the frontend (#308) does the same thing a scheduled trigger would.

**Safety, in the order this function checks it, and why each check exists:**

1. *Driver availability.* Only Postgres has a driver in this product (:data:`_HARVESTABLE`,
   matching ``api/routers/sources.py``'s own ``_TESTABLE_SOURCE_TYPES`` -- Snowflake is in
   the contract's ``HARVESTABLE_SOURCE_TYPES`` as a forward-looking claim; no
   ``snowflake-connector-python`` dependency exists yet to make that real).
2. *Permission level.* A ``read_write``-declared source is refused -- the wire contract's
   own words are "never exposed to a model" (``PermissionLevel``'s docstring), and a
   harvest is the first step of exactly that exposure.
3. *The SSRF guard* (duplicated from ``api/routers/sources.py``'s ``_refused_host_reason``,
   not imported -- this codebase's own convention for a small helper two call sites need;
   see that module's docstring). A registered source's host was checked once, at
   registration, by ``test-connection`` -- but nothing forces a caller to have called it
   first, and a harvest is a second, independent connection attempt that deserves the same
   guard, not an inherited assumption.
4. *The schema-collision guard*
   (:meth:`~egpt_retail.semantic.store.SemanticGraphStore.schema_owned_by_other_source`).
   Two data sources registered with the same ``schema`` name would otherwise silently
   prune and overwrite each other's tables -- see that method's docstring, and
   ``write_harvest``'s, for why table identity does not yet include ``source_id`` (#307).

Every failure updates the source's ``harvest_state`` and returns a clean, non-raising
``StepOutput`` -- a harvest that cannot proceed is a fact about the source, not a workflow
crash, the same distinction R7's executors draw between a guard refusal and an error.
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.settings import RetailSettings
from egpt_retail.configure.secrets import build_secret_store
from egpt_retail.configure.store import DataSourceStore
from egpt_retail.contracts.data_source import (
    DataSource,
    DataSourceType,
    HarvestState,
    PermissionLevel,
)
from egpt_retail.semantic.harvest import harvest_schema
from egpt_retail.semantic.store import SemanticGraphStore

__all__ = ["harvest_source"]

log = logging.getLogger("egpt_retail.executors.data_source_harvest")

#: Source types with an actual driver in this product today -- mirrors
#: ``api/routers/sources.py``'s ``_TESTABLE_SOURCE_TYPES`` exactly, and for the same reason:
#: the contract's own ``HARVESTABLE_SOURCE_TYPES`` also names Snowflake, which has no driver
#: dependency in this product yet either. Duplicated rather than imported -- see the module
#: docstring.
_HARVESTABLE = frozenset({DataSourceType.POSTGRES})

_CONNECT_TIMEOUT_SECONDS = 10.0


def _refused_host_reason(host: str) -> Optional[str]:
    """Duplicated from ``api/routers/sources.py`` -- see that function's docstring for the
    policy (refuses loopback/link-local, checked against the resolved address; allows
    RFC1918) and the module docstring here for why it is duplicated, not imported."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    for info in infos:
        addr = ip_address(info[4][0])
        if addr.is_loopback or addr.is_link_local:
            return (
                "refused: this host resolves to a loopback or link-local address, which "
                "this deployment does not connect to."
            )
    return None


def _neo4j_driver(settings: RetailSettings) -> Any:
    if not settings.retail_neo4j_password:
        raise RuntimeError(
            "EGPT_RETAIL_NEO4J_PASSWORD is not set, so the harvest has no graph to go to."
        )
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.retail_neo4j_uri,
        auth=(settings.retail_neo4j_user, settings.retail_neo4j_password),
    )


def _open_source_connection(source: DataSource, credential: Any) -> Any:
    import psycopg

    return psycopg.connect(
        host=source.host,
        port=source.port or 5432,
        dbname=source.database_name,
        user=credential.username,
        password=credential.password or credential.api_key,
        connect_timeout=int(_CONNECT_TIMEOUT_SECONDS),
        sslmode=source.ssl_mode or "require",
    )


def _fail(
    store: DataSourceStore,
    source_id: str,
    state: HarvestState,
    message: str,
    *,
    run_id: Optional[str] = None,
) -> StepOutput:
    store.set_harvest_state(source_id, state, run_id=run_id)
    log.warning("harvest_source(%s): %s", source_id, message)
    return StepOutput(
        content={"source_id": source_id, "ok": False, "detail": message},
        success=False,
        error=message,
        stop=True,
    )


async def harvest_source(step_input: StepInput) -> StepOutput:
    """The ``harvest-source`` workflow's one step. Reads ``source_id`` from
    ``step_input.additional_data`` -- required, since a harvest with no source to harvest is
    not a smaller version of this operation, it is a different request entirely."""
    additional_data = step_input.additional_data or {}
    source_id = additional_data.get("source_id")
    if not source_id:
        return StepOutput(
            content={"ok": False, "detail": "additional_data.source_id is required"},
            success=False,
            error="additional_data.source_id is required",
            stop=True,
        )

    run_id = (
        step_input.workflow_session.session_id if step_input.workflow_session else None
    )

    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        store = DataSourceStore(connection)
        secret_store = build_secret_store(settings, connection)

        source = store.get(source_id)
        if source is None:
            return StepOutput(
                content={"ok": False, "detail": f"No such source: {source_id!r}."},
                success=False,
                error=f"No such source: {source_id!r}.",
                stop=True,
            )

        if source.source_type not in _HARVESTABLE:
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                f"No harvest driver for source_type={source.source_type.value!r} yet.",
                run_id=run_id,
            )
        if source.permission_level is PermissionLevel.READ_WRITE:
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                "Refusing to harvest a source registered with read_write permission -- "
                "never exposed to a model, per the wire contract's own rule.",
                run_id=run_id,
            )

        store.set_harvest_state(source_id, HarvestState.BUILDING, run_id=run_id)

        refusal = _refused_host_reason(source.host)
        if refusal is not None:
            return _fail(store, source_id, HarvestState.ERROR, refusal, run_id=run_id)

        try:
            credential = secret_store.read(source.secret_ref)
        except Exception as exc:
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                f"Could not read the stored credential: {exc}",
                run_id=run_id,
            )

        try:
            warehouse_connection = _open_source_connection(source, credential)
        except Exception as exc:
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                f"Could not connect: {exc}",
                run_id=run_id,
            )

        schema_name = source.schema_name or "public"
        try:
            warehouse_connection.autocommit = True
            harvest = harvest_schema(
                warehouse_connection, schema_name, source=source.name, sample=False
            )
        finally:
            warehouse_connection.close()

        if not harvest.tables:
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                f"Schema {schema_name!r} harvested as empty -- check the schema name and "
                "the credential's grants.",
                run_id=run_id,
            )

        driver = _neo4j_driver(settings)
        try:
            graph = SemanticGraphStore(
                driver=driver, database=settings.retail_neo4j_database
            )
            graph.ensure_indexes()
            conflict = graph.schema_owned_by_other_source(schema_name, source_id)
            if conflict is not None:
                return _fail(
                    store,
                    source_id,
                    HarvestState.NEEDS_REVIEW,
                    f"Schema {schema_name!r} already holds tables from a different "
                    f"source ({conflict!r}). Two sources sharing one schema name is not "
                    "supported yet -- rename this source's schema.",
                    run_id=run_id,
                )
            version = datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")
            tables_written = graph.write_harvest(
                harvest, snapshot_version=version, source_id=source_id
            )
        finally:
            driver.close()

        store.set_harvest_state(
            source_id, HarvestState.MODELLED, run_id=run_id, mark_harvested_now=True
        )
        return StepOutput(
            content={
                "source_id": source_id,
                "ok": True,
                "schema": schema_name,
                "tables": tables_written,
                "columns": harvest.column_count,
                "snapshot_version": version,
            }
        )
    finally:
        connection.close()
