"""``DataSourceStore`` -- the ``configure.data_source`` table in the control-plane database
(#304).

Same self-provisioning shape every other control-plane store in this product uses
(``kpi/store.py``'s ``KpiRegistryStore``, ``semantic/proposals.py``'s ``ProposalStore``,
``playground/store.py``'s ``PlaygroundStore``): ``ensure_schema()`` behind an advisory lock,
a connection injected and owned by the caller, sync methods with an ``asyncio.to_thread``
async twin. This is the first store built on the shared ``db/schema_utils.ensure_schema``
helper rather than hand-rolling the lock-then-DDL-then-commit sequence again.

**Credentials never reach this table.** Every write path here takes (or already holds) a
``secret_ref`` and a ``secrets.SecretStore``, never a
:class:`~egpt_retail.contracts.data_source.Credential` -- the actual material is written to
and read from ``secrets.py`` at the two points that need it (:meth:`create`, :meth:`update`
when rotating), and this class only ever persists the opaque reference. See
``contracts/data_source.py``'s module docstring for why that split exists.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Tuple

from egpt_retail.contracts.data_source import (
    AuthMethod,
    ConnectionTestResult,
    DataSource,
    DataSourceCreate,
    DataSourceStatus,
    DataSourceType,
    DataSourceUpdate,
    HarvestState,
    validate_create,
    validate_credential,
)
from egpt_retail.db.schema_utils import aensure_schema, ensure_schema

if TYPE_CHECKING:
    from egpt_retail.configure.secrets import SecretStore

__all__ = ["DATA_SOURCE_SCHEMA", "DataSourceStore"]

log = logging.getLogger("egpt_retail.configure.store")

DATA_SOURCE_SCHEMA = "configure"

#: Fixed order, used by every SELECT/INSERT/RETURNING below and by :func:`_row_to_data_source`
#: -- one list, so the two can never drift out of sync with each other.
_COLUMNS: Tuple[str, ...] = (
    "id",
    "name",
    "source_type",
    "host",
    "port",
    "database_name",
    "schema_name",
    "auth_method",
    "secret_ref",
    "status",
    "harvest_state",
    "created_by",
    "created_at",
    "updated_at",
    "last_tested_at",
    "last_test_ok",
    "last_test_message",
    "last_harvest_run_id",
    "last_harvest_at",
)
_COLUMN_LIST = ", ".join(_COLUMNS)


def _ddl(schema: str) -> Tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.data_source (
            id                   text PRIMARY KEY,
            name                 text        NOT NULL,
            source_type          text        NOT NULL,
            host                 text        NOT NULL,
            port                 integer,
            database_name        text        NOT NULL,
            schema_name          text        NOT NULL DEFAULT '',
            auth_method          text        NOT NULL,
            secret_ref           text        NOT NULL,
            status               text        NOT NULL DEFAULT 'registered',
            harvest_state        text        NOT NULL DEFAULT 'not_started',
            created_by           text        NOT NULL DEFAULT '',
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            last_tested_at       timestamptz,
            last_test_ok         boolean,
            last_test_message    text        NOT NULL DEFAULT '',
            last_harvest_run_id  text,
            last_harvest_at      timestamptz,
            CONSTRAINT data_source_status_valid
                CHECK (status IN ('registered', 'testing', 'connected', 'error')),
            CONSTRAINT data_source_harvest_state_valid
                CHECK (harvest_state IN
                    ('not_started', 'building', 'modelled', 'needs_review', 'error'))
        )""",
        f"""CREATE INDEX IF NOT EXISTS data_source_created_by_idx
            ON {schema}.data_source (created_by, created_at DESC)""",
        f"""COMMENT ON TABLE {schema}.data_source IS
            'A user-registered data source (#302, #304): connection shape plus a secret_ref '
            'pointing at secrets.py''s SecretStore, never the credential itself.'""",
    )


def _new_id() -> str:
    return f"ds_{uuid.uuid4().hex[:12]}"


def _row_to_data_source(row: Sequence[Any]) -> DataSource:
    """The inverse of the ``.value`` writes in :meth:`DataSourceStore.create` and every
    ``UPDATE`` below -- every enum column comes back out of psycopg as a plain ``str``, so
    each is re-wrapped here rather than at every call site that reads a row."""
    values = dict(zip(_COLUMNS, row))
    return DataSource(
        id=values["id"],
        name=values["name"],
        source_type=DataSourceType(values["source_type"]),
        host=values["host"],
        port=values["port"],
        database_name=values["database_name"],
        schema_name=values["schema_name"],
        auth_method=AuthMethod(values["auth_method"]),
        secret_ref=values["secret_ref"],
        status=DataSourceStatus(values["status"]),
        harvest_state=HarvestState(values["harvest_state"]),
        created_by=values["created_by"],
        created_at=values["created_at"],
        updated_at=values["updated_at"],
        last_tested_at=values["last_tested_at"],
        last_test_ok=values["last_test_ok"],
        last_test_message=values["last_test_message"],
        last_harvest_run_id=values["last_harvest_run_id"],
        last_harvest_at=values["last_harvest_at"],
    )


class DataSourceStore:
    """The ``configure.data_source`` table. The connection is injected; the caller owns its
    lifetime -- same convention as every other control-plane store in this product."""

    def __init__(self, connection: Any, schema: str = DATA_SOURCE_SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        ensure_schema(self._connection, _ddl(self._schema), "egpt_retail.configure.ddl")

    async def aensure_schema(self) -> None:
        await aensure_schema(
            self._connection, _ddl(self._schema), "egpt_retail.configure.ddl"
        )

    # --- create / read -------------------------------------------------------------------

    def create(
        self, create: DataSourceCreate, secret_store: "SecretStore"
    ) -> DataSource:
        """Validates, writes the credential to ``secret_store``, then inserts the row.

        The secret is written first and the row second -- an orphaned secret with no row
        pointing at it (if the insert somehow failed) is inert and cleaned up by nothing
        today, but harmless; a row that names a ``secret_ref`` nothing backs would instead
        be a source that looks registered and fails every read, silently, the first time it
        is used. The safer failure mode is the one this ordering produces.
        """
        validate_create(create)
        secret_ref = secret_store.write(create.name, create.credential)
        now = datetime.now(timezone.utc)
        row = (
            _new_id(),
            create.name,
            create.source_type.value,
            create.host,
            create.port,
            create.database_name,
            create.schema_name,
            create.credential.auth_method.value,
            secret_ref,
            DataSourceStatus.REGISTERED.value,
            HarvestState.NOT_STARTED.value,
            create.created_by,
            now,
            now,
            None,
            None,
            "",
            None,
            None,
        )
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.data_source ({_COLUMN_LIST})
                    VALUES ({", ".join(["%s"] * len(_COLUMNS))})
                    RETURNING {_COLUMN_LIST}""",
                row,
            )
            inserted = cursor.fetchone()
        self._connection.commit()
        return _row_to_data_source(inserted)

    async def acreate(
        self, create: DataSourceCreate, secret_store: "SecretStore"
    ) -> DataSource:
        return await asyncio.to_thread(self.create, create, secret_store)

    def get(self, source_id: str) -> Optional[DataSource]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMN_LIST} FROM {self._schema}.data_source WHERE id = %s",
                (source_id,),
            )
            row = cursor.fetchone()
        return _row_to_data_source(row) if row is not None else None

    async def aget(self, source_id: str) -> Optional[DataSource]:
        return await asyncio.to_thread(self.get, source_id)

    def list(self, created_by: Optional[str] = None) -> Tuple[DataSource, ...]:
        with self._connection.cursor() as cursor:
            if created_by is None:
                cursor.execute(
                    f"SELECT {_COLUMN_LIST} FROM {self._schema}.data_source "
                    "ORDER BY created_at DESC"
                )
            else:
                cursor.execute(
                    f"SELECT {_COLUMN_LIST} FROM {self._schema}.data_source "
                    "WHERE created_by = %s ORDER BY created_at DESC",
                    (created_by,),
                )
            rows = cursor.fetchall()
        return tuple(_row_to_data_source(row) for row in rows)

    async def alist(self, created_by: Optional[str] = None) -> Tuple[DataSource, ...]:
        return await asyncio.to_thread(self.list, created_by)

    # --- update ------------------------------------------------------------------------

    def update(
        self, source_id: str, patch: DataSourceUpdate, secret_store: "SecretStore"
    ) -> Optional[DataSource]:
        """Applies ``patch``. A field left ``None`` on ``patch`` is left unchanged -- see
        :class:`~egpt_retail.contracts.data_source.DataSourceUpdate`'s own docstring on why
        that matters for ``credential`` specifically. Returns ``None`` if ``source_id``
        does not exist, the same "absent means not found" shape :meth:`get` uses.
        """
        existing = self.get(source_id)
        if existing is None:
            return None

        set_clauses = ["updated_at = %s"]
        params: List[Any] = [datetime.now(timezone.utc)]
        old_secret_ref: Optional[str] = None

        if patch.name is not None:
            set_clauses.append("name = %s")
            params.append(patch.name)
        if patch.host is not None:
            set_clauses.append("host = %s")
            params.append(patch.host)
        if patch.port is not None:
            set_clauses.append("port = %s")
            params.append(patch.port)
        if patch.database_name is not None:
            set_clauses.append("database_name = %s")
            params.append(patch.database_name)
        if patch.schema_name is not None:
            set_clauses.append("schema_name = %s")
            params.append(patch.schema_name)
        if patch.credential is not None:
            validate_credential(existing.source_type, patch.credential)
            new_secret_ref = secret_store.write(existing.name, patch.credential)
            set_clauses.append("auth_method = %s")
            params.append(patch.credential.auth_method.value)
            set_clauses.append("secret_ref = %s")
            params.append(new_secret_ref)
            old_secret_ref = existing.secret_ref

        params.append(source_id)
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.data_source SET {", ".join(set_clauses)}
                    WHERE id = %s
                    RETURNING {_COLUMN_LIST}""",
                params,
            )
            row = cursor.fetchone()
        self._connection.commit()

        if old_secret_ref is not None:
            self._best_effort_delete_secret(secret_store, old_secret_ref)
        return _row_to_data_source(row) if row is not None else None

    async def aupdate(
        self, source_id: str, patch: DataSourceUpdate, secret_store: "SecretStore"
    ) -> Optional[DataSource]:
        return await asyncio.to_thread(self.update, source_id, patch, secret_store)

    # --- test / harvest status, written by #305 and #306 --------------------------------

    def record_test_result(
        self, source_id: str, result: ConnectionTestResult
    ) -> Optional[DataSource]:
        """Called by #305's ``POST /sources/{id}/test-connection``. A successful test moves
        status to ``connected``; a failed one to ``error`` -- ``testing`` is a transient
        state the caller sets itself before running the probe, never written here."""
        status = DataSourceStatus.CONNECTED if result.ok else DataSourceStatus.ERROR
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.data_source
                    SET status = %s, last_tested_at = %s, last_test_ok = %s,
                        last_test_message = %s, updated_at = %s
                    WHERE id = %s
                    RETURNING {_COLUMN_LIST}""",
                (
                    status.value,
                    result.tested_at,
                    result.ok,
                    result.message,
                    datetime.now(timezone.utc),
                    source_id,
                ),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _row_to_data_source(row) if row is not None else None

    async def arecord_test_result(
        self, source_id: str, result: ConnectionTestResult
    ) -> Optional[DataSource]:
        return await asyncio.to_thread(self.record_test_result, source_id, result)

    def set_status(
        self, source_id: str, status: DataSourceStatus
    ) -> Optional[DataSource]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.data_source
                    SET status = %s, updated_at = %s
                    WHERE id = %s
                    RETURNING {_COLUMN_LIST}""",
                (status.value, datetime.now(timezone.utc), source_id),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _row_to_data_source(row) if row is not None else None

    async def aset_status(
        self, source_id: str, status: DataSourceStatus
    ) -> Optional[DataSource]:
        return await asyncio.to_thread(self.set_status, source_id, status)

    def set_harvest_state(
        self,
        source_id: str,
        state: HarvestState,
        *,
        run_id: Optional[str] = None,
        mark_harvested_now: bool = False,
    ) -> Optional[DataSource]:
        """Called by #306's harvest workflow as it runs, and on completion. ``run_id`` is
        recorded whenever given (a new run replaces the last one on record); the harvest
        timestamp only advances when ``mark_harvested_now`` is set, which the workflow does
        on its terminal step, not on every intermediate state change."""
        set_clauses = ["harvest_state = %s", "updated_at = %s"]
        params: List[Any] = [state.value, datetime.now(timezone.utc)]
        if run_id is not None:
            set_clauses.append("last_harvest_run_id = %s")
            params.append(run_id)
        if mark_harvested_now:
            set_clauses.append("last_harvest_at = %s")
            params.append(datetime.now(timezone.utc))
        params.append(source_id)
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.data_source SET {", ".join(set_clauses)}
                    WHERE id = %s
                    RETURNING {_COLUMN_LIST}""",
                params,
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _row_to_data_source(row) if row is not None else None

    async def aset_harvest_state(
        self,
        source_id: str,
        state: HarvestState,
        *,
        run_id: Optional[str] = None,
        mark_harvested_now: bool = False,
    ) -> Optional[DataSource]:
        return await asyncio.to_thread(
            self.set_harvest_state,
            source_id,
            state,
            run_id=run_id,
            mark_harvested_now=mark_harvested_now,
        )

    # --- delete ------------------------------------------------------------------------

    def delete(self, source_id: str, secret_store: "SecretStore") -> bool:
        """Deletes the row, then best-effort deletes its secret. In that order deliberately:
        the row is the thing an API caller can see and reason about, so it stops existing
        first; a secret that outlives its row for a moment is inert (nothing can resolve it
        without a ``secret_ref``, and nothing keeps one around once the row is gone), while
        the reverse order would leave a row referencing a secret that is already gone if the
        row delete then failed.
        """
        existing = self.get(source_id)
        if existing is None:
            return False
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self._schema}.data_source WHERE id = %s", (source_id,)
            )
        self._connection.commit()
        self._best_effort_delete_secret(secret_store, existing.secret_ref)
        return True

    async def adelete(self, source_id: str, secret_store: "SecretStore") -> bool:
        return await asyncio.to_thread(self.delete, source_id, secret_store)

    @staticmethod
    def _best_effort_delete_secret(
        secret_store: "SecretStore", secret_ref: str
    ) -> None:
        try:
            secret_store.delete(secret_ref)
        except Exception:
            log.warning(
                "Failed to delete secret %r after it was superseded or its data source was "
                "deleted. It is orphaned but unreachable through the API from here on.",
                secret_ref,
                exc_info=True,
            )
