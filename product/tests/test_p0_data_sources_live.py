"""#304 -- ``DataSourceStore`` and ``LocalEncryptedSecretStore`` against real Postgres.

``test_p0_data_sources.py`` covers everything that needs no database. What needs one is the
part this PR's whole claim rests on -- the credential guarantees -- so they are asserted here,
in a suite ``integration.yml`` runs, rather than only in the manually-run
``cookbook/94_retail/P0_data_source_registration.py`` walkthrough:

- no credential attribute on the returned ``DataSource``, and none in the stored row;
- the stored ciphertext never contains the plaintext password;
- ``read`` recovers exactly what was written;
- rotating a credential changes ``secret_ref`` and deletes the old secret;
- deleting a source deletes its secret too;
- a patch ``create`` would have refused is refused, and writes nothing;
- concurrent rotations never leave an orphaned secret behind;
- the async twins of every public method behave like the sync ones;
- the ``CHECK`` constraints on the closed enum columns are enforced, including on a table
  that was provisioned before they existed.

Gated the way ``test_r15_rehearsal_live.py`` is: ``EGPT_INTEGRATION_POSTGRES``, skipped
otherwise so the unit run stays database-free. Locally, against the compose stack's Postgres:

    EGPT_INTEGRATION_POSTGRES=1 pytest product/tests/test_p0_data_sources_live.py

The control-plane DSN is ``EGPT_RETAIL_CP_DSN`` (default ``postgresql://ai:ai@localhost:5532/ai``,
what ``integration.yml`` sets). Everything written is keyed under a ``created_by`` prefixed
``livetest_p0_`` and removed at the end of each test, secrets included.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Optional, Set

import pytest

from egpt_retail.configure.secrets import (
    LocalEncryptedSecretStore,
    SecretNotFoundError,
)
from egpt_retail.configure.store import DataSourceConflictError, DataSourceStore
from egpt_retail.contracts.data_source import (
    AuthMethod,
    ConnectionTestResult,
    Credential,
    DataSourceCreate,
    DataSourceStatus,
    DataSourceType,
    DataSourceUpdate,
    DataSourceValidationError,
    HarvestState,
)

_LIVE = os.environ.get("EGPT_INTEGRATION_POSTGRES")
pytestmark = pytest.mark.skipif(
    not _LIVE, reason="needs EGPT_INTEGRATION_POSTGRES=1 with a live Postgres"
)

DSN = os.environ.get("EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai")

PASSWORD = "s3cr3t-p0-live-password"


def _credential(
    password: str = PASSWORD, username: str = "reporting_user"
) -> Credential:
    return Credential(
        auth_method=AuthMethod.USERNAME_PASSWORD, username=username, password=password
    )


def _refs(connection: Any) -> Set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT ref FROM configure.local_secret")
        rows = cursor.fetchall()
    connection.rollback()
    return {row[0] for row in rows}


@pytest.fixture
def env() -> Iterator[SimpleNamespace]:
    psycopg = pytest.importorskip("psycopg")
    fernet = pytest.importorskip("cryptography.fernet")
    key = fernet.Fernet.generate_key().decode("utf-8")
    connection = psycopg.connect(DSN)
    secrets = LocalEncryptedSecretStore(connection, key)
    secrets.ensure_schema()
    sources = DataSourceStore(connection)
    sources.ensure_schema()
    marker = f"livetest_p0_{uuid.uuid4().hex[:12]}"

    def create(password: str = PASSWORD, **overrides: Any) -> Any:
        fields: dict = dict(
            name="P0 live Postgres",
            source_type=DataSourceType.POSTGRES,
            host="warehouse.internal",
            database_name="analytics",
            schema_name="public",
            credential=_credential(password),
            created_by=marker,
        )
        fields.update(overrides)
        return sources.create(DataSourceCreate(**fields), secrets)

    def second_connection() -> Any:
        """A separate session, as a concurrent request would be -- with its own stores
        sharing this test's encryption key, so it can rotate the same source."""
        other = psycopg.connect(DSN)
        return SimpleNamespace(
            connection=other,
            sources=DataSourceStore(other),
            secrets=LocalEncryptedSecretStore(other, key),
        )

    others: list = []

    def open_second() -> Any:
        other = second_connection()
        others.append(other)
        return other

    yield SimpleNamespace(
        connection=connection,
        secrets=secrets,
        sources=sources,
        marker=marker,
        key=key,
        create=create,
        open_second=open_second,
    )

    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT secret_ref FROM configure.data_source WHERE created_by = %s",
            (marker,),
        )
        leftover = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            "DELETE FROM configure.data_source WHERE created_by = %s", (marker,)
        )
    connection.commit()
    for ref in leftover:
        secrets.delete(ref)
    for other in others:
        other.connection.close()
    connection.close()


class TestCredentialGuarantees:
    def test_no_credential_attribute_on_the_returned_data_source(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        for name in ("password", "api_key", "credential", "oauth_client_secret"):
            assert not hasattr(created, name)
        assert created.status is DataSourceStatus.REGISTERED
        assert created.harvest_state is HarvestState.NOT_STARTED
        assert created.username == "reporting_user"

    def test_the_stored_row_holds_no_plaintext_password(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        with env.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM configure.data_source WHERE id = %s", (created.id,)
            )
            row = cursor.fetchone()
        assert PASSWORD not in repr(row)

    def test_the_stored_ciphertext_does_not_contain_the_plaintext(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        with env.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ciphertext FROM configure.local_secret WHERE ref = %s",
                (created.secret_ref,),
            )
            (ciphertext,) = cursor.fetchone()
        assert PASSWORD.encode("utf-8") not in bytes(ciphertext)

    def test_read_recovers_the_exact_credential_written(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        recovered = env.secrets.read(created.secret_ref)
        assert recovered == _credential()

    def test_rotation_changes_the_ref_and_deletes_the_old_secret(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        old_ref = created.secret_ref

        rotated = env.sources.update(
            created.id,
            DataSourceUpdate(credential=_credential("a-rotated-password", "rotated")),
            env.secrets,
        )

        assert rotated.secret_ref != old_ref
        assert rotated.username == "rotated"
        with pytest.raises(SecretNotFoundError):
            env.secrets.read(old_ref)
        assert env.secrets.read(rotated.secret_ref).password == "a-rotated-password"

    def test_an_update_without_a_credential_leaves_the_secret_alone(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        renamed = env.sources.update(
            created.id, DataSourceUpdate(name="Renamed"), env.secrets
        )
        assert renamed.name == "Renamed"
        assert renamed.secret_ref == created.secret_ref
        assert env.secrets.read(created.secret_ref).password == PASSWORD

    def test_deleting_a_source_deletes_its_secret_too(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        assert env.sources.delete(created.id, env.secrets) is True
        assert env.sources.get(created.id) is None
        with pytest.raises(SecretNotFoundError):
            env.secrets.read(created.secret_ref)

    def test_missing_source_is_absent_not_an_error(self, env: SimpleNamespace) -> None:
        assert env.sources.get("ds_does_not_exist") is None
        assert env.sources.delete("ds_does_not_exist", env.secrets) is False
        assert (
            env.sources.update(
                "ds_does_not_exist", DataSourceUpdate(name="x"), env.secrets
            )
            is None
        )

    def test_status_and_harvest_bookkeeping(self, env: SimpleNamespace) -> None:
        created = env.create()
        tested = env.sources.record_test_result(
            created.id,
            ConnectionTestResult(
                ok=True, message="reachable", tested_at=datetime.now(timezone.utc)
            ),
        )
        assert tested.status is DataSourceStatus.CONNECTED
        harvested = env.sources.set_harvest_state(
            created.id, HarvestState.MODELLED, run_id="run_1", mark_harvested_now=True
        )
        assert harvested.harvest_state is HarvestState.MODELLED
        assert harvested.last_harvest_run_id == "run_1"
        assert harvested.last_harvest_at is not None


class TestUpdateValidation:
    """``update()`` used to write ``name=""``, ``host=""``, ``port=99999`` straight through."""

    @pytest.mark.parametrize(
        "patch",
        [
            DataSourceUpdate(name=""),
            DataSourceUpdate(host="  "),
            DataSourceUpdate(database_name=""),
            DataSourceUpdate(port=99999),
            DataSourceUpdate(port=0),
            DataSourceUpdate(ssl_mode="requires"),
            DataSourceUpdate(name="", host="", port=99999),
        ],
    )
    def test_a_bad_patch_is_refused_and_changes_nothing(
        self, env: SimpleNamespace, patch: DataSourceUpdate
    ) -> None:
        created = env.create()
        before = _refs(env.connection)

        with pytest.raises(DataSourceValidationError):
            env.sources.update(created.id, patch, env.secrets)

        assert env.sources.get(created.id) == created
        assert _refs(env.connection) == before

    def test_a_bad_patch_carrying_a_credential_writes_no_secret(
        self, env: SimpleNamespace
    ) -> None:
        """Validation runs before the secret write, so a refused patch cannot orphan one."""
        created = env.create()
        before = _refs(env.connection)

        with pytest.raises(DataSourceValidationError):
            env.sources.update(
                created.id,
                DataSourceUpdate(host="", credential=_credential("never-stored")),
                env.secrets,
            )

        assert _refs(env.connection) == before
        assert env.secrets.read(created.secret_ref).password == PASSWORD

    def test_an_incomplete_rotated_credential_is_refused(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        with pytest.raises(DataSourceValidationError):
            env.sources.update(
                created.id,
                DataSourceUpdate(
                    credential=Credential(
                        auth_method=AuthMethod.USERNAME_PASSWORD, username="only"
                    )
                ),
                env.secrets,
            )
        assert env.sources.get(created.id) == created

    def test_a_valid_patch_applies(self, env: SimpleNamespace) -> None:
        created = env.create()
        patched = env.sources.update(
            created.id,
            DataSourceUpdate(host="db2.internal", port=5433, ssl_mode="verify-full"),
            env.secrets,
        )
        assert (patched.host, patched.port, patched.ssl_mode) == (
            "db2.internal",
            5433,
            "verify-full",
        )

    def test_create_refuses_an_ssl_mode_typo(self, env: SimpleNamespace) -> None:
        with pytest.raises(DataSourceValidationError):
            env.create(ssl_mode="requires")


class _RacingSecretStore(LocalEncryptedSecretStore):
    """A secret store whose ``write`` runs ``competitor`` right after the secret is written --
    the window in which, for a real second request, another rotation would land between this
    rotation's read of ``secret_ref`` and its ``UPDATE``."""

    def __init__(
        self,
        connection: Any,
        key: str,
        competitor: Callable[[], None],
        *,
        every_time: bool,
    ) -> None:
        super().__init__(connection, key)
        self._competitor = competitor
        self._every_time = every_time
        self.writes = 0

    def write(self, name_hint: str, credential: Credential) -> str:
        ref = super().write(name_hint, credential)
        self.writes += 1
        if self._every_time or self.writes == 1:
            self._competitor()
        return ref


class TestConcurrentRotation:
    """Two rotations that read the same ``secret_ref`` used to orphan the loser's newly
    written secret -- unreferenced by any row and never deleted."""

    def _racing(
        self,
        env: SimpleNamespace,
        source_id: str,
        competitor: Callable[[Any], None],
        *,
        every_time: bool,
    ) -> _RacingSecretStore:
        other = env.open_second()
        return _RacingSecretStore(
            env.connection,
            env.key,
            lambda: competitor(other),
            every_time=every_time,
        )

    def test_a_lost_rotation_retries_and_leaves_no_orphan(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        before = _refs(env.connection)

        def competitor(other: Any) -> None:
            other.sources.update(
                created.id,
                DataSourceUpdate(credential=_credential("competitor-password")),
                other.secrets,
            )

        racing = self._racing(env, created.id, competitor, every_time=False)
        rotated = env.sources.update(
            created.id,
            DataSourceUpdate(credential=_credential("outer-password")),
            racing,
        )

        # The outer rotation lost once, retried against the competitor's row, and won.
        assert racing.writes == 2
        assert env.secrets.read(rotated.secret_ref).password == "outer-password"
        after = _refs(env.connection)
        assert created.secret_ref not in after
        # Exactly one secret was added and it is the one the row points at: the first
        # attempt's secret and the competitor's superseded one are both gone.
        assert after - before == {rotated.secret_ref}

    def test_a_rotation_that_keeps_losing_raises_and_leaves_no_orphan(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        before = _refs(env.connection)
        counter = {"n": 0}

        def competitor(other: Any) -> None:
            counter["n"] += 1
            other.sources.update(
                created.id,
                DataSourceUpdate(credential=_credential(f"competitor-{counter['n']}")),
                other.secrets,
            )

        racing = self._racing(env, created.id, competitor, every_time=True)
        with pytest.raises(DataSourceConflictError):
            env.sources.update(
                created.id,
                DataSourceUpdate(credential=_credential("outer-password")),
                racing,
            )

        current = env.sources.get(created.id)
        assert env.secrets.read(current.secret_ref).password == "competitor-3"
        assert _refs(env.connection) - before == {current.secret_ref}

    def test_a_source_deleted_mid_rotation_returns_none_and_leaves_no_orphan(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        before = _refs(env.connection)

        def competitor(other: Any) -> None:
            other.sources.delete(created.id, other.secrets)

        racing = self._racing(env, created.id, competitor, every_time=False)
        result = env.sources.update(
            created.id,
            DataSourceUpdate(credential=_credential("too-late")),
            racing,
        )

        assert result is None
        assert env.sources.get(created.id) is None
        assert _refs(env.connection) - before == set()


class TestEnumConstraints:
    _INSERT = """INSERT INTO configure.data_source
        (id, name, source_type, host, database_name, auth_method, secret_ref, created_by)
        VALUES (%s, 'x', %s, 'h', 'd', %s, 'local:none', %s)"""

    def _insert(self, env: SimpleNamespace, source_type: str, auth_method: str) -> None:
        with env.connection.cursor() as cursor:
            cursor.execute(
                self._INSERT,
                (f"ds_{uuid.uuid4().hex[:12]}", source_type, auth_method, env.marker),
            )
        env.connection.commit()

    @pytest.mark.parametrize(
        ("source_type", "auth_method"),
        [("oracle", "username_password"), ("postgres", "kerberos")],
    )
    def test_a_value_outside_the_enum_is_refused_by_the_database(
        self, env: SimpleNamespace, source_type: str, auth_method: str
    ) -> None:
        psycopg = pytest.importorskip("psycopg")
        with pytest.raises(psycopg.errors.CheckViolation):
            self._insert(env, source_type, auth_method)
        env.connection.rollback()

    def test_a_valid_direct_insert_is_accepted(self, env: SimpleNamespace) -> None:
        self._insert(env, "postgres", "api_key")

    def test_ensure_schema_adds_the_constraints_to_an_existing_table(
        self, env: SimpleNamespace
    ) -> None:
        """A table provisioned before the constraints existed keeps its old shape through
        ``CREATE TABLE IF NOT EXISTS``; the guarded ``ALTER`` in ``_ddl`` is what closes it."""
        psycopg = pytest.importorskip("psycopg")
        names = ("data_source_source_type_valid", "data_source_auth_method_valid")
        try:
            with env.connection.cursor() as cursor:
                for name in names:
                    cursor.execute(
                        f"ALTER TABLE configure.data_source DROP CONSTRAINT {name}"
                    )
            env.connection.commit()
            # Constraint gone: the bad value is (wrongly) accepted, as on an old table.
            self._insert(env, "oracle", "kerberos")
            with env.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM configure.data_source WHERE created_by = %s",
                    (env.marker,),
                )
            env.connection.commit()

            env.sources.ensure_schema()

            with pytest.raises(psycopg.errors.CheckViolation):
                self._insert(env, "oracle", "postgres")
            env.connection.rollback()
        finally:
            env.connection.rollback()
            env.sources.ensure_schema()

    def test_ensure_schema_is_idempotent(self, env: SimpleNamespace) -> None:
        env.sources.ensure_schema()
        env.sources.ensure_schema()


class TestAsyncTwins:
    """Every ``a*`` method is a thin ``asyncio.to_thread`` wrapper -- exercised end to end
    so a refactor that changes a sync signature without its twin's call site fails here."""

    def test_async_round_trip(self, env: SimpleNamespace) -> None:
        async def scenario() -> None:
            await env.secrets.aensure_schema()
            await env.sources.aensure_schema()

            created = await env.sources.acreate(
                DataSourceCreate(
                    name="Async source",
                    source_type=DataSourceType.POSTGRES,
                    host="warehouse.internal",
                    database_name="analytics",
                    credential=_credential(),
                    created_by=env.marker,
                ),
                env.secrets,
            )
            assert (await env.sources.aget(created.id)) == created
            listed = await env.sources.alist(created_by=env.marker)
            assert [row.id for row in listed] == [created.id]

            tested = await env.sources.arecord_test_result(
                created.id,
                ConnectionTestResult(
                    ok=False,
                    message="unreachable",
                    tested_at=datetime.now(timezone.utc),
                ),
            )
            assert tested.status is DataSourceStatus.ERROR
            status = await env.sources.aset_status(created.id, DataSourceStatus.TESTING)
            assert status.status is DataSourceStatus.TESTING
            harvest = await env.sources.aset_harvest_state(
                created.id, HarvestState.BUILDING, run_id="run_async"
            )
            assert harvest.harvest_state is HarvestState.BUILDING

            old_ref = created.secret_ref
            rotated = await env.sources.aupdate(
                created.id,
                DataSourceUpdate(
                    name="Async renamed", credential=_credential("async-2")
                ),
                env.secrets,
            )
            assert rotated.name == "Async renamed"
            assert (await env.secrets.aread(rotated.secret_ref)).password == "async-2"
            with pytest.raises(SecretNotFoundError):
                await env.secrets.aread(old_ref)

            assert (await env.sources.adelete(created.id, env.secrets)) is True
            assert (await env.sources.aget(created.id)) is None
            with pytest.raises(SecretNotFoundError):
                await env.secrets.aread(rotated.secret_ref)

        asyncio.run(scenario())

    def test_async_secret_store_write_read_delete(self, env: SimpleNamespace) -> None:
        async def scenario() -> Optional[str]:
            ref = await env.secrets.awrite("hint", _credential("async-secret"))
            assert (await env.secrets.aread(ref)).password == "async-secret"
            await env.secrets.adelete(ref)
            # Deleting an already-deleted secret is a no-op, not an error.
            await env.secrets.adelete(ref)
            with pytest.raises(SecretNotFoundError):
                await env.secrets.aread(ref)
            return ref

        asyncio.run(scenario())
