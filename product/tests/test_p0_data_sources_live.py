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
- deleting a source never orphans a secret, even when a rotation lands mid-delete;
- a failed insert leaves no secret behind and the connection usable;
- padded free-text fields are stored stripped;
- a secret store whose ``delete`` fails never changes the outcome of ``create``, ``update`` or
  ``delete``, nor masks the error being handled;
- the ``CHECK`` constraints on the closed enum columns are enforced, including on a table
  that was provisioned before they existed, and are refreshed when an enum gains a member.

Gated the way ``test_r15_rehearsal_live.py`` is: ``EGPT_INTEGRATION_POSTGRES``, skipped
otherwise so the unit run stays database-free. Locally, against the compose stack's Postgres:

    EGPT_INTEGRATION_POSTGRES=1 pytest product/tests/test_p0_data_sources_live.py

The control-plane DSN is ``EGPT_RETAIL_CP_DSN`` (default ``postgresql://ai:ai@localhost:5532/ai``,
what ``integration.yml`` sets). Everything written is keyed under a ``created_by`` prefixed
``livetest_p0_`` and removed at the end of each test, secrets included.
"""

from __future__ import annotations

import asyncio
import logging
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
from egpt_retail.configure import store as store_module
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


class _RotateAfterGet(DataSourceStore):
    """A store whose ``get`` runs ``rotate`` right after reading -- the window a
    read-then-delete would be exposed in, where a rotation commits between the read of
    ``secret_ref`` and the ``DELETE``."""

    def __init__(self, connection: Any, rotate: Callable[[], None]) -> None:
        super().__init__(connection)
        self._rotate = rotate
        self.reads = 0

    def get(self, source_id: str) -> Any:
        found = super().get(source_id)
        self.reads += 1
        if self.reads == 1:
            self._rotate()
        return found


class TestDeleteNeverOrphansASecret:
    """``delete()`` used to delete the ``secret_ref`` it had read before an unconditional
    ``DELETE``: a rotation that committed in between left its new secret unreferenced and
    never deleted -- the same leak the compare-and-swap in ``update()`` closes for rotation."""

    def test_a_rotation_landing_mid_delete_leaves_no_orphan(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        other = env.open_second()
        before = _refs(env.connection)

        def rotate() -> None:
            other.sources.update(
                created.id,
                DataSourceUpdate(credential=_credential("rotated-mid-delete")),
                other.secrets,
            )

        racing = _RotateAfterGet(env.connection, rotate)
        assert racing.delete(created.id, env.secrets) is True

        assert env.sources.get(created.id) is None
        # Whatever the rotation did, no secret it wrote is left behind.
        assert _refs(env.connection) - before == set()
        assert created.secret_ref not in _refs(env.connection)

    def test_the_secret_deleted_is_the_current_one_after_a_rotation(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        rotated = env.sources.update(
            created.id, DataSourceUpdate(credential=_credential("second")), env.secrets
        )
        assert env.sources.delete(created.id, env.secrets) is True
        with pytest.raises(SecretNotFoundError):
            env.secrets.read(rotated.secret_ref)

    def test_a_second_delete_reports_that_nothing_was_deleted(
        self, env: SimpleNamespace
    ) -> None:
        """Including a concurrent delete that got there first: only the caller whose
        ``DELETE`` removed the row reports ``True`` and cleans up its secret."""
        created = env.create()
        assert env.sources.delete(created.id, env.secrets) is True
        assert env.sources.delete(created.id, env.secrets) is False


class TestFailedCreateLeavesNothingBehind:
    """``create()`` writes the secret before the row, so a failed insert used to leave a live
    credential with no row pointing at it -- and the connection in an aborted transaction."""

    def test_a_failed_insert_deletes_the_secret_and_leaves_the_connection_usable(
        self, env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        psycopg = pytest.importorskip("psycopg")
        existing = env.create()
        before = _refs(env.connection)
        monkeypatch.setattr(
            store_module, "_new_id", lambda: existing.id
        )  # id collision

        with pytest.raises(psycopg.errors.UniqueViolation):
            env.create(name="Collides")

        assert _refs(env.connection) == before  # the secret it wrote was deleted again
        # Not left in an aborted transaction: the next statement on it works.
        assert env.sources.get(existing.id) == existing

    def test_a_check_violation_is_cleaned_up_the_same_way(
        self, env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        psycopg = pytest.importorskip("psycopg")
        before = _refs(env.connection)
        # A harvest state the CHECK refuses, so the INSERT fails after the secret was written.
        monkeypatch.setattr(
            store_module,
            "HarvestState",
            SimpleNamespace(NOT_STARTED=_BadValue("not_a_state")),
        )

        with pytest.raises(psycopg.errors.CheckViolation):
            env.create()

        assert _refs(env.connection) == before
        assert env.sources.list(created_by=env.marker) == ()


class _BadValue:
    """Stands in for an enum member whose ``.value`` the ``CHECK`` refuses."""

    def __init__(self, value: str) -> None:
        self.value = value


class TestWhitespaceIsNormalised:
    def test_padded_fields_are_stored_stripped(self, env: SimpleNamespace) -> None:
        created = env.create(
            name="  Padded  ",
            host=" warehouse.internal ",
            database_name=" analytics\n",
            schema_name=" public ",
        )
        stored = env.sources.get(created.id)
        assert (stored.name, stored.host, stored.database_name, stored.schema_name) == (
            "Padded",
            "warehouse.internal",
            "analytics",
            "public",
        )

    def test_a_padded_update_is_stored_stripped(self, env: SimpleNamespace) -> None:
        created = env.create()
        patched = env.sources.update(
            created.id, DataSourceUpdate(host="  db2.internal "), env.secrets
        )
        assert patched.host == "db2.internal"

    def test_whitespace_only_is_still_refused_after_stripping(
        self, env: SimpleNamespace
    ) -> None:
        with pytest.raises(DataSourceValidationError):
            env.create(host="   ")

    def test_a_password_with_surrounding_whitespace_is_kept_exactly(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create(password="  spaced password  ")
        assert env.secrets.read(created.secret_ref).password == "  spaced password  "


class _RecordingSecretStore(LocalEncryptedSecretStore):
    def __init__(self, connection: Any, key: str) -> None:
        super().__init__(connection, key)
        self.hints: list = []

    def write(self, name_hint: str, credential: Credential) -> str:
        self.hints.append(name_hint)
        return super().write(name_hint, credential)


class TestSecretNameHint:
    """The hint exists so a person looking at Key Vault can tell which source a secret
    belongs to -- a PATCH that renames and rotates must label it with the *new* name."""

    def test_a_rename_and_rotate_labels_the_secret_with_the_new_name(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        recording = _RecordingSecretStore(env.connection, env.key)
        env.sources.update(
            created.id,
            DataSourceUpdate(name="New name", credential=_credential("rotated")),
            recording,
        )
        assert recording.hints == ["New name"]

    def test_a_rotation_without_a_rename_uses_the_existing_name(
        self, env: SimpleNamespace
    ) -> None:
        created = env.create()
        recording = _RecordingSecretStore(env.connection, env.key)
        env.sources.update(
            created.id, DataSourceUpdate(credential=_credential("rotated")), recording
        )
        assert recording.hints == [created.name]


class _FailingDeleteSecretStore(LocalEncryptedSecretStore):
    """A secret store whose ``delete`` always fails (an unreachable vault, say). Everything
    else is the real thing, so the secrets it fails to delete really are left behind."""

    def delete(self, secret_ref: str) -> None:
        raise RuntimeError("vault unreachable")


class TestSecretCleanupFailureNeverChangesTheOutcome:
    """Deleting a superseded or orphaned secret is best-effort. If it fails, the operation the
    caller asked for must still have the outcome it would otherwise have had -- and, on an
    error path, the caller must still see the *original* error, not the cleanup's."""

    @staticmethod
    def _sweep(env: SimpleNamespace, before: Any) -> None:
        """Removes the secrets these tests deliberately failed to delete, so they do not
        accumulate in the shared control-plane database."""
        for ref in _refs(env.connection) - before:
            env.secrets.delete(ref)

    def test_create_raises_the_insert_error_not_the_cleanup_error(
        self,
        env: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        psycopg = pytest.importorskip("psycopg")
        existing = env.create()
        before = _refs(env.connection)
        failing = _FailingDeleteSecretStore(env.connection, env.key)
        monkeypatch.setattr(store_module, "_new_id", lambda: existing.id)
        try:
            with caplog.at_level(logging.WARNING, logger="egpt_retail.configure.store"):
                with pytest.raises(psycopg.errors.UniqueViolation):  # not RuntimeError
                    env.sources.create(
                        DataSourceCreate(
                            name="Collides",
                            source_type=DataSourceType.POSTGRES,
                            host="warehouse.internal",
                            database_name="analytics",
                            credential=_credential(),
                            created_by=env.marker,
                        ),
                        failing,
                    )
            # The connection is still usable, and the failed cleanup was reported.
            assert env.sources.get(existing.id) == existing
            assert any("orphaned" in r.getMessage() for r in caplog.records)
        finally:
            self._sweep(env, before)

    def test_a_rotation_still_succeeds_when_the_old_secret_cannot_be_deleted(
        self, env: SimpleNamespace, caplog: pytest.LogCaptureFixture
    ) -> None:
        created = env.create()
        before = _refs(env.connection)
        failing = _FailingDeleteSecretStore(env.connection, env.key)
        try:
            with caplog.at_level(logging.WARNING, logger="egpt_retail.configure.store"):
                rotated = env.sources.update(
                    created.id,
                    DataSourceUpdate(credential=_credential("rotated")),
                    failing,
                )
            assert rotated is not None and rotated.secret_ref != created.secret_ref
            assert env.secrets.read(rotated.secret_ref).password == "rotated"
            # The old secret is left behind (that is the cost of a failed cleanup) and logged.
            assert created.secret_ref in _refs(env.connection)
            assert any(created.secret_ref in r.getMessage() for r in caplog.records)
        finally:
            self._sweep(env, before | {created.secret_ref})
            env.secrets.delete(created.secret_ref)

    def test_a_rotation_that_lost_its_race_still_retries_when_cleanup_fails(
        self, env: SimpleNamespace
    ) -> None:
        """The loser's own secret cannot be deleted, so the retry has to go ahead regardless."""
        created = env.create()
        other = env.open_second()
        before = _refs(env.connection)

        class _RacingAndFailing(_RacingSecretStore):
            def delete(self, secret_ref: str) -> None:
                raise RuntimeError("vault unreachable")

        def competitor() -> None:
            other.sources.update(
                created.id,
                DataSourceUpdate(credential=_credential("competitor")),
                other.secrets,
            )

        racing = _RacingAndFailing(
            env.connection, env.key, competitor, every_time=False
        )
        try:
            rotated = env.sources.update(
                created.id,
                DataSourceUpdate(credential=_credential("outer")),
                racing,
            )
            assert racing.writes == 2  # lost once, retried, won
            assert env.secrets.read(rotated.secret_ref).password == "outer"
        finally:
            self._sweep(env, before)

    def test_delete_still_reports_true_when_the_secret_cannot_be_deleted(
        self, env: SimpleNamespace, caplog: pytest.LogCaptureFixture
    ) -> None:
        created = env.create()
        failing = _FailingDeleteSecretStore(env.connection, env.key)
        try:
            with caplog.at_level(logging.WARNING, logger="egpt_retail.configure.store"):
                assert env.sources.delete(created.id, failing) is True
            assert env.sources.get(created.id) is None  # the row is gone regardless
            assert any(created.secret_ref in r.getMessage() for r in caplog.records)
        finally:
            env.secrets.delete(created.secret_ref)


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

    @staticmethod
    def _constraint(env: SimpleNamespace, column: str) -> Any:
        """``(oid, definition)`` of the column's CHECK, or ``None`` if it has none."""
        with env.connection.cursor() as cursor:
            cursor.execute(
                """SELECT c.oid, pg_get_constraintdef(c.oid) FROM pg_constraint c
                   WHERE c.conrelid = 'configure.data_source'::regclass AND c.conname = %s""",
                (f"data_source_{column}_valid",),
            )
            row = cursor.fetchone()
        env.connection.rollback()
        return row

    @pytest.mark.parametrize("column", [c for c, _ in store_module._ENUM_COLUMNS])
    def test_ensure_schema_adds_a_missing_constraint_to_an_existing_table(
        self, env: SimpleNamespace, column: str
    ) -> None:
        """A table provisioned before a constraint existed keeps its old shape through
        ``CREATE TABLE IF NOT EXISTS``; the guarded refresh in ``_ddl`` is what closes it."""
        try:
            with env.connection.cursor() as cursor:
                cursor.execute(
                    f"ALTER TABLE configure.data_source "
                    f"DROP CONSTRAINT data_source_{column}_valid"
                )
            env.connection.commit()
            assert self._constraint(env, column) is None

            env.sources.ensure_schema()

            assert self._constraint(env, column) is not None
        finally:
            env.connection.rollback()
            env.sources.ensure_schema()

    def test_a_dropped_source_type_constraint_admits_a_bad_value_until_restored(
        self, env: SimpleNamespace
    ) -> None:
        psycopg = pytest.importorskip("psycopg")
        try:
            with env.connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE configure.data_source "
                    "DROP CONSTRAINT data_source_source_type_valid"
                )
            env.connection.commit()
            self._insert(env, "oracle", "api_key")  # accepted: the constraint is gone
            with env.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM configure.data_source WHERE created_by = %s",
                    (env.marker,),
                )
            env.connection.commit()

            env.sources.ensure_schema()

            with pytest.raises(psycopg.errors.CheckViolation):
                self._insert(env, "oracle", "api_key")
            env.connection.rollback()
        finally:
            env.connection.rollback()
            env.sources.ensure_schema()

    def test_a_constraint_missing_a_member_the_enum_gained_is_refreshed(
        self, env: SimpleNamespace
    ) -> None:
        """A database provisioned before ``needs_review`` (or any later member) existed would
        refuse it with a ``CheckViolation`` on first use. Boot replaces the constraint."""
        created = env.create()
        try:
            with env.connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE configure.data_source "
                    "DROP CONSTRAINT data_source_harvest_state_valid"
                )
                cursor.execute(
                    "ALTER TABLE configure.data_source "
                    "ADD CONSTRAINT data_source_harvest_state_valid "
                    "CHECK (harvest_state IN ('not_started', 'building')) NOT VALID"
                )
            env.connection.commit()
            assert "needs_review" not in self._constraint(env, "harvest_state")[1]

            env.sources.ensure_schema()

            assert "needs_review" in self._constraint(env, "harvest_state")[1]
            updated = env.sources.set_harvest_state(
                created.id, HarvestState.NEEDS_REVIEW
            )
            assert updated.harvest_state is HarvestState.NEEDS_REVIEW
        finally:
            env.connection.rollback()
            env.sources.ensure_schema()

    def test_a_legacy_row_can_never_make_boot_fail(self, env: SimpleNamespace) -> None:
        """The refreshed constraint is ``NOT VALID``: a row written under an older vocabulary
        (here a state the enum no longer has) is not re-checked, so provisioning succeeds."""
        psycopg = pytest.importorskip("psycopg")
        try:
            with env.connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE configure.data_source "
                    "DROP CONSTRAINT data_source_harvest_state_valid"
                )
                cursor.execute(
                    """INSERT INTO configure.data_source
                       (id, name, source_type, host, database_name, auth_method,
                        secret_ref, created_by, harvest_state)
                       VALUES (%s, 'legacy', 'postgres', 'h', 'd', 'api_key',
                               'local:none', %s, 'legacy_state')""",
                    (f"ds_{uuid.uuid4().hex[:12]}", env.marker),
                )
            env.connection.commit()

            env.sources.ensure_schema()  # must not raise on the legacy row

            assert self._constraint(env, "harvest_state") is not None
            with pytest.raises(psycopg.errors.CheckViolation):
                with env.connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO configure.data_source
                           (id, name, source_type, host, database_name, auth_method,
                            secret_ref, created_by, harvest_state)
                           VALUES (%s, 'new', 'postgres', 'h', 'd', 'api_key',
                                   'local:none', %s, 'another_bad_state')""",
                        (f"ds_{uuid.uuid4().hex[:12]}", env.marker),
                    )
            env.connection.rollback()
        finally:
            env.connection.rollback()
            with env.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM configure.data_source WHERE created_by = %s",
                    (env.marker,),
                )
            env.connection.commit()
            env.sources.ensure_schema()

    def test_a_current_constraint_is_left_alone_at_boot(
        self, env: SimpleNamespace
    ) -> None:
        """The refresh runs at every boot and must not rewrite a constraint that is already
        right -- a rewrite takes a lock on the table and rescans it, for nothing."""
        before = {c: self._constraint(env, c) for c, _ in store_module._ENUM_COLUMNS}
        env.sources.ensure_schema()
        after = {c: self._constraint(env, c) for c, _ in store_module._ENUM_COLUMNS}
        assert before == after  # same oid and definition: not dropped and re-added

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
