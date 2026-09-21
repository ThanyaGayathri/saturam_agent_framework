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

1. *Driver availability* (``DataSource.harvestable``). Only Postgres has a driver in this
   product -- Snowflake is in the contract's ``HARVESTABLE_SOURCE_TYPES`` as a
   forward-looking claim, and ``HARVEST_DRIVER_SOURCE_TYPES`` is the narrower set that has a
   driver. The executor and the public property read the same set, so they cannot disagree.
2. *Permission level.* A ``read_write``-declared source is refused -- the wire contract's
   own words are "never exposed to a model" (``PermissionLevel``'s docstring), and a
   harvest is the first step of exactly that exposure.
3. *The SSRF guard* (``configure/host_guard.py``'s :func:`~egpt_retail.configure.host_guard.
   check_host`, the one policy ``api/routers/sources.py``'s test-connection route uses too). A
   registered source's host was checked once, at registration, by ``test-connection`` -- but
   nothing forces a caller to have called it first, and a harvest is a second, independent
   connection attempt that deserves the same guard, not an inherited assumption. Loopback,
   link-local and unspecified (``0.0.0.0``/``::``) addresses are refused, every address the
   host resolves to is checked, and resolution is bounded, so a name served by an unresponsive
   DNS server cannot stall the step. The host is resolved **once** and the connection is then
   made to the addresses that passed (``hostaddr=``), so a hostname cannot answer with a public
   address for the check and rebind to loopback or a cloud metadata address for the connect.
   This module used to carry its own copy of the check; the copy had no ``is_unspecified`` and
   no timeout, and the two drifted -- there is nothing here to keep in step now.
4. *The schema-collision guard* (and the reclaim of a stale claim), enforced by
   :meth:`~egpt_retail.semantic.store.SemanticGraphStore.write_harvest` itself, which raises
   :class:`~egpt_retail.semantic.store.SchemaOwnedByOtherSource` from inside its write
   transaction. Two data sources registered with the same ``schema`` name would otherwise
   silently prune and overwrite each other's tables -- see ``write_harvest``'s docstring for
   why table identity does not include ``source_id`` (reads are scoped by it instead, #307,
   so schema names must be unique across sources), and why the check cannot be a separate
   read made beforehand. **A claim held by a source that no longer exists is stale, and is
   reclaimed, not refused.** Deleting a source used to leave its tables in the graph, still
   tagged with its id, so the next source registered with the same schema was refused on behalf
   of a source that was gone -- and stopped in ``needs_review`` with nothing in the UI to fix
   it. The route now removes a deleted source's tables, but a graph orphaned before that (or by a
   delete that raced a running harvest) is still out there, so :func:`_write_harvest` treats an
   owner that is not a registered source as stale: it removes that owner's tables and writes
   again, once. Ids are random and never reused, so "not registered" cannot become "registered"
   between the check and the removal. Two owners are never reclaimed: a registered source (a real
   conflict -- ``needs_review``, naming it) and the operator's own warehouse (``""``, which has
   no source id to remove and which nothing here may delete).
5. *The source still exists when the write is done.* A source deleted while ``building`` has
   its row gone by the time this writes. After the write the row is looked up again; if it is
   gone the tables just written are removed (the delete route's own sweep may already have run)
   and the harvest ends without marking anything modelled.

Every failure after the source is marked ``building`` -- including an unexpected error
reading the schema or writing the graph -- moves ``harvest_state`` to a terminal state and
returns a clean, non-raising ``StepOutput``. A harvest that cannot proceed is a fact about the
source, not a workflow crash, the same distinction R7's executors draw between a guard
refusal and an error; and a source must never be left showing ``building`` for a run that has
already ended.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.settings import RetailSettings
from egpt_retail.configure.host_guard import check_host
from egpt_retail.configure.secrets import build_secret_store
from egpt_retail.configure.store import DataSourceStore
from egpt_retail.contracts.data_source import (
    DataSource,
    HarvestState,
    PermissionLevel,
)
from egpt_retail.semantic.harvest import harvest_schema
from egpt_retail.semantic.store import SchemaOwnedByOtherSource, SemanticGraphStore

__all__ = ["harvest_source"]

log = logging.getLogger("egpt_retail.executors.data_source_harvest")

_CONNECT_TIMEOUT_SECONDS = 10.0


def _owner_description(store: DataSourceStore, owner: str) -> str:
    """Who holds a claimed schema, for a message a person reads: the source's name and id if it
    is a registered source, the deployment's own warehouse for ``""``."""
    if not owner:
        return "the deployment's own warehouse"
    source = store.get(owner)
    if source is None:
        return f"another source ({owner})"
    return f"the source {source.name!r} ({owner})"


def _write_harvest(
    graph: SemanticGraphStore,
    store: DataSourceStore,
    harvest: Any,
    version: str,
    source_id: str,
) -> int:
    """Write the harvest, reclaiming the schema first if its claim is stale -- see the module
    docstring. Raises :class:`SchemaOwnedByOtherSource` for a claim that is real."""
    try:
        return graph.write_harvest(
            harvest, snapshot_version=version, source_id=source_id
        )
    except SchemaOwnedByOtherSource as claim:
        owner = claim.owner
        # The operator's own warehouse ("") and a registered source are real claims. So is a
        # claim by this very source, which the guard never raises but a caller must not act on.
        if not owner or owner == source_id or store.get(owner) is not None:
            raise
    log.warning(
        "harvest_source(%s): schema %r is claimed by %s, which is not a registered source; "
        "removing its tables and writing again",
        source_id,
        harvest.schema,
        owner,
    )
    try:
        graph.delete_source_tables(owner)
    except Exception as exc:
        raise RuntimeError(
            f"could not remove the tables left behind by the deleted source {owner!r}: {exc}"
        ) from exc
    # Once. A second refusal is a different owner, and is reported as it is.
    return graph.write_harvest(harvest, snapshot_version=version, source_id=source_id)


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


def _open_source_connection(
    source: DataSource, credential: Any, addresses: Tuple[str, ...]
) -> Any:
    """Connect to ``source`` at one of ``addresses``, the ones
    :func:`~egpt_retail.configure.host_guard.check_host` already vetted, trying each in order
    as the driver would have. ``host`` is still passed so ``verify-full`` checks the certificate against
    the name, not the address."""
    import psycopg

    last_error: Exception = RuntimeError("no vetted address to connect to")
    for address in addresses:
        try:
            return psycopg.connect(
                host=source.host,
                hostaddr=address,
                port=source.port or 5432,
                dbname=source.database_name,
                user=credential.username,
                password=credential.password or credential.api_key,
                connect_timeout=int(_CONNECT_TIMEOUT_SECONDS),
                sslmode=source.ssl_mode or "require",
            )
        except psycopg.OperationalError as exc:
            last_error = exc
    raise last_error


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

        if not source.harvestable:
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

        # On a worker thread: resolution is bounded, but a bound is still up to a few seconds of
        # blocking, and this step runs on the framework's event loop.
        checked = await asyncio.to_thread(check_host, source.host, source.port or 5432)
        if checked.message is not None:
            return _fail(
                store, source_id, HarvestState.ERROR, checked.message, run_id=run_id
            )
        addresses = checked.addresses

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
            warehouse_connection = _open_source_connection(
                source, credential, addresses
            )
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
        except Exception as exc:
            log.exception(
                "harvest_source(%s): reading %r failed", source_id, schema_name
            )
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                f"Could not read schema {schema_name!r}: {exc}",
                run_id=run_id,
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

        try:
            driver = _neo4j_driver(settings)
        except Exception as exc:
            log.exception("harvest_source(%s): no graph driver", source_id)
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                f"Could not reach the semantic graph: {exc}",
                run_id=run_id,
            )
        version = datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")
        deleted_meanwhile = False
        try:
            graph = SemanticGraphStore(
                driver=driver, database=settings.retail_neo4j_database
            )
            graph.ensure_indexes()
            tables_written = _write_harvest(graph, store, harvest, version, source_id)
            if store.get(source_id) is None:
                # Deleted while this was running. The delete route cleans the graph, but this
                # write can land after it: take back what was just written.
                graph.delete_source_tables(source_id)
                deleted_meanwhile = True
        except SchemaOwnedByOtherSource as exc:
            return _fail(
                store,
                source_id,
                HarvestState.NEEDS_REVIEW,
                f"Schema {harvest.schema!r} is already used by "
                f"{_owner_description(store, exc.owner)}. Point this source at a "
                "different schema, then retry modelling.",
                run_id=run_id,
            )
        except Exception as exc:
            log.exception("harvest_source(%s): graph write failed", source_id)
            return _fail(
                store,
                source_id,
                HarvestState.ERROR,
                f"Could not write the harvest to the semantic graph: {exc}",
                run_id=run_id,
            )
        finally:
            driver.close()

        if deleted_meanwhile:
            log.info(
                "harvest_source(%s): the source was deleted while it was harvested; its "
                "tables were removed again",
                source_id,
            )
            return StepOutput(
                content={
                    "source_id": source_id,
                    "ok": False,
                    "detail": "The source was deleted while it was being harvested.",
                },
                success=False,
                error="The source was deleted while it was being harvested.",
                stop=True,
            )

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
