"""#304 -- the parts of the data-source contract and secret encoding that need no database.

The round trip against real Postgres (credential storage, rotation, deletion, the async
twins, the rotation race) is proved by ``test_p0_data_sources_live.py``, gated by
``EGPT_INTEGRATION_POSTGRES`` and run by ``integration.yml`` -- the same split every other
store in this product uses (see ``semantic/proposals.py``'s own note on why).
``cookbook/94_retail/P0_data_source_registration.py`` is the same scenario as a readable
walkthrough. What is tested here is what needs no database: validation logic a wrong change
would break silently, and the two properties the whole design rests on -- a credential is
never accidentally printable, and no shape that is stored or returned has a place to hold one.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from egpt_retail.configure import store as store_module
from egpt_retail.configure.secrets import _credential_from_dict, _credential_to_dict
from egpt_retail.contracts.data_source import (
    SSL_MODES,
    AuthMethod,
    Credential,
    DataSource,
    DataSourceCreate,
    DataSourceStatus,
    DataSourceType,
    DataSourceUpdate,
    DataSourceValidationError,
    HarvestState,
    PermissionLevel,
    normalize_create,
    normalize_update,
    validate_create,
    validate_credential,
    validate_update,
)

_CONFIGURE_SCHEMAS = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "api"
    / "openapi"
    / "schemas"
    / "configure.yaml"
)


def _postgres_credential(**overrides: object) -> Credential:
    fields = dict(
        auth_method=AuthMethod.USERNAME_PASSWORD, username="reader", password="hunter2"
    )
    fields.update(overrides)
    return Credential(**fields)  # type: ignore[arg-type]


def _create(**overrides: object) -> DataSourceCreate:
    fields = dict(
        name="Warehouse",
        source_type=DataSourceType.POSTGRES,
        host="db.internal",
        database_name="analytics",
        credential=_postgres_credential(),
    )
    fields.update(overrides)
    return DataSourceCreate(**fields)  # type: ignore[arg-type]


class TestCredentialRedaction:
    def test_repr_omits_password(self) -> None:
        assert "hunter2" not in repr(_postgres_credential())

    def test_repr_omits_api_key(self) -> None:
        credential = Credential(
            auth_method=AuthMethod.API_KEY, api_key="sk-do-not-print"
        )
        assert "sk-do-not-print" not in repr(credential)

    def test_repr_omits_oauth_secret(self) -> None:
        credential = Credential(
            auth_method=AuthMethod.OAUTH,
            oauth_client_id="client-123",
            oauth_client_secret="do-not-print-me",
        )
        rendered = repr(credential)
        assert "do-not-print-me" not in rendered
        # __repr__ redacts the whole credential, not just the fields that are individually
        # secret -- simpler than deciding field-by-field, and a non-secret id has no reason
        # to appear in a log line that exists to prove nothing sensitive leaked.
        assert "client-123" not in rendered

    def test_str_uses_the_same_redacted_repr(self) -> None:
        assert "hunter2" not in str(_postgres_credential())


class TestValidateCredential:
    def test_username_password_requires_both(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_credential(
                DataSourceType.POSTGRES,
                Credential(auth_method=AuthMethod.USERNAME_PASSWORD, username="reader"),
            )

    def test_api_key_requires_a_key(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_credential(
                DataSourceType.POSTGRES, Credential(auth_method=AuthMethod.API_KEY)
            )

    def test_oauth_requires_client_id_and_secret(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_credential(
                DataSourceType.SALESFORCE,
                Credential(auth_method=AuthMethod.OAUTH, oauth_client_id="client-only"),
            )

    def test_auth_method_must_be_offered_for_the_source_type(self) -> None:
        """Salesforce's form only ever offers OAuth (#302's forms are the source of truth
        here) -- a username/password credential attached to a Salesforce row would have no
        code path that could ever use it."""
        with pytest.raises(DataSourceValidationError):
            validate_credential(DataSourceType.SALESFORCE, _postgres_credential())

    def test_a_valid_credential_raises_nothing(self) -> None:
        validate_credential(DataSourceType.POSTGRES, _postgres_credential())


class TestValidateCreate:
    def test_empty_name_is_refused(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_create(_create(name="   "))

    def test_empty_host_is_refused(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_create(_create(host=""))

    def test_port_out_of_range_is_refused(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_create(_create(port=70000))

    def test_port_zero_is_refused(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_create(_create(port=0))

    def test_credential_mismatched_with_source_type_is_refused(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_create(
                _create(
                    source_type=DataSourceType.SALESFORCE,
                    credential=_postgres_credential(),
                )
            )

    def test_a_valid_create_raises_nothing(self) -> None:
        validate_create(_create())

    def test_ssl_mode_typo_is_refused_at_registration(self) -> None:
        """``ssl_mode`` reaches the driver as ``sslmode=``; a typo would otherwise register
        cleanly and only fail deep inside a later connection attempt."""
        with pytest.raises(DataSourceValidationError) as raised:
            validate_create(_create(ssl_mode="requires"))
        assert raised.value.field == "ssl_mode"

    def test_underscore_spellings_of_verify_modes_are_refused(self) -> None:
        """libpq spells them ``verify-ca``/``verify-full``; the underscore spelling is not a
        value the driver accepts, so it must not be one the store accepts either."""
        for spelling in ("verify_ca", "verify_full"):
            with pytest.raises(DataSourceValidationError):
                validate_create(_create(ssl_mode=spelling))

    @pytest.mark.parametrize("mode", sorted(SSL_MODES))
    def test_every_libpq_ssl_mode_is_accepted(self, mode: str) -> None:
        validate_create(_create(ssl_mode=mode))

    def test_the_ssl_modes_are_libpqs(self) -> None:
        assert SSL_MODES == {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }


def _existing(**overrides: object) -> DataSource:
    now = datetime.now(timezone.utc)
    fields = dict(
        id="ds_test",
        name="Warehouse",
        source_type=DataSourceType.POSTGRES,
        host="db.internal",
        database_name="analytics",
        auth_method=AuthMethod.USERNAME_PASSWORD,
        secret_ref="local:abc",
        status=DataSourceStatus.REGISTERED,
        harvest_state=HarvestState.NOT_STARTED,
        created_by="",
        created_at=now,
        updated_at=now,
    )
    fields.update(overrides)
    return DataSource(**fields)  # type: ignore[arg-type]


class TestValidateUpdate:
    """``update()`` used to apply a patch with no validation at all, so ``name=""``,
    ``host=""`` or ``port=99999`` persisted -- rows ``validate_create`` exists to refuse."""

    @pytest.mark.parametrize(
        ("patch", "field"),
        [
            (DataSourceUpdate(name=""), "name"),
            (DataSourceUpdate(name="   "), "name"),
            (DataSourceUpdate(host=""), "host"),
            (DataSourceUpdate(database_name=" "), "database_name"),
            (DataSourceUpdate(port=0), "port"),
            (DataSourceUpdate(port=99999), "port"),
            (DataSourceUpdate(ssl_mode="requires"), "ssl_mode"),
            (DataSourceUpdate(ssl_mode="verify_full"), "ssl_mode"),
        ],
    )
    def test_a_patch_create_would_refuse_is_refused(
        self, patch: DataSourceUpdate, field: str
    ) -> None:
        with pytest.raises(DataSourceValidationError) as raised:
            validate_update(_existing(), patch)
        assert raised.value.field == field

    def test_a_rotated_credential_is_checked_against_the_existing_source_type(
        self,
    ) -> None:
        salesforce = _existing(source_type=DataSourceType.SALESFORCE)
        with pytest.raises(DataSourceValidationError):
            validate_update(
                salesforce, DataSourceUpdate(credential=_postgres_credential())
            )

    def test_an_incomplete_rotated_credential_is_refused(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_update(
                _existing(),
                DataSourceUpdate(
                    credential=Credential(
                        auth_method=AuthMethod.USERNAME_PASSWORD, username="reader"
                    )
                ),
            )

    def test_an_empty_patch_is_valid(self) -> None:
        validate_update(_existing(), DataSourceUpdate())

    def test_a_valid_patch_raises_nothing(self) -> None:
        validate_update(
            _existing(),
            DataSourceUpdate(
                name="Renamed",
                host="db2.internal",
                port=5433,
                ssl_mode="verify-full",
                schema_name="",
                credential=_postgres_credential(),
            ),
        )


class TestNoCredentialShapeIsStoredOrReturned:
    """The design's central claim, checked structurally so it needs no database: nothing that
    is persisted or returned has a place to hold credential material. A future field added to
    ``DataSource`` or a new column on the table that looks like one fails here."""

    #: Substrings that mark a credential-shaped name. ``secret_ref`` is the one field that
    #: legitimately mentions a secret -- it is the opaque handle, not the material.
    _CREDENTIAL_SHAPED = (
        "password",
        "passwd",
        "api_key",
        "apikey",
        "token",
        "credential",
        "secret",
        "private_key",
    )
    _ALLOWED = {"secret_ref"}

    def _offending(self, names: "list[str]") -> "list[str]":
        return [
            name
            for name in names
            if name not in self._ALLOWED
            and any(marker in name.lower() for marker in self._CREDENTIAL_SHAPED)
        ]

    def test_data_source_has_no_credential_shaped_field(self) -> None:
        names = [field.name for field in dataclasses.fields(DataSource)]
        assert self._offending(names) == []
        assert "secret_ref" in names

    def test_the_table_has_no_credential_shaped_column(self) -> None:
        assert self._offending(list(store_module._COLUMNS)) == []

    def test_the_table_ddl_declares_no_credential_shaped_column(self) -> None:
        create_table = next(
            statement
            for statement in store_module._ddl("configure")
            if statement.lstrip().startswith("CREATE TABLE")
        )
        declared = [
            line.split()[0]
            for line in create_table.splitlines()[1:]
            if line.strip()
            and not line.strip().startswith(("CONSTRAINT", "CHECK"))
            and line.split()[0].isidentifier()
        ]
        assert "secret_ref" in declared
        assert self._offending(declared) == []

    def test_a_returned_data_source_has_no_credential_attribute(self) -> None:
        source = _existing()
        for name in ("password", "api_key", "credential", "oauth_client_secret"):
            assert not hasattr(source, name)


class TestDdlEnumConstraints:
    """Every closed enum column has a ``CHECK`` generated from its enum, so none can drift
    from the Python vocabulary -- three were once hand-copied string lists, and adding a
    ``HarvestState`` member without touching the DDL would have been a ``CheckViolation`` on
    the first write of it."""

    _ENUMS = [
        ("source_type", DataSourceType),
        ("permission_level", PermissionLevel),
        ("auth_method", AuthMethod),
        ("status", DataSourceStatus),
        ("harvest_state", HarvestState),
    ]

    @staticmethod
    def _ddl_text() -> str:
        return "\n".join(store_module._ddl("configure"))

    def test_every_closed_enum_column_is_covered(self) -> None:
        """The table of (column, enum) the constraints are generated from names every column
        that has a Python enum -- so adding an enum column without a CHECK fails here."""
        assert {column for column, _ in store_module._ENUM_COLUMNS} == {
            column for column, _ in self._ENUMS
        }

    @pytest.mark.parametrize(("column", "enum"), _ENUMS)
    def test_the_check_lists_exactly_the_enums_members(
        self, column: str, enum: type
    ) -> None:
        text = self._ddl_text()
        assert f"data_source_{column}_valid" in text
        expected = ", ".join(f"'{member.value}'" for member in enum)
        assert f"CHECK ({column} IN ({expected}))" in text

    @pytest.mark.parametrize(("column", "enum"), _ENUMS)
    def test_the_check_is_declared_inline_for_a_fresh_table(
        self, column: str, enum: type
    ) -> None:
        create_table = next(
            statement
            for statement in store_module._ddl("configure")
            if statement.lstrip().startswith("CREATE TABLE")
        )
        assert f"CONSTRAINT data_source_{column}_valid" in create_table

    def test_an_already_provisioned_table_gets_a_refresh_for_every_constraint(
        self,
    ) -> None:
        """``CREATE TABLE IF NOT EXISTS`` skips an existing table, so each constraint also
        ships as a guarded statement that adds it, or replaces it if it is missing a member."""
        refreshes = [
            statement
            for statement in store_module._ddl("configure")
            if statement.lstrip().startswith("DO $$")
        ]
        assert len(refreshes) == len(self._ENUMS)
        for (column, _), statement in zip(store_module._ENUM_COLUMNS, refreshes):
            assert "pg_constraint" in statement
            assert f"data_source_{column}_valid" in statement
            assert "ADD CONSTRAINT" in statement


class TestNormalisation:
    """``validate_create`` judges ``name``/``host``/``database_name`` stripped, so what is
    stored must be stripped too -- ``" db.internal "`` used to register cleanly and fail
    later, at name resolution, with nothing pointing at the padding."""

    def test_create_strips_the_free_text_fields(self) -> None:
        create = normalize_create(
            _create(
                name="  Warehouse ",
                host=" db.internal ",
                database_name="\tanalytics\n",
                schema_name=" silver ",
            )
        )
        assert (create.name, create.host, create.database_name, create.schema_name) == (
            "Warehouse",
            "db.internal",
            "analytics",
            "silver",
        )

    def test_the_credential_is_never_touched(self) -> None:
        """Whitespace in a password is significant."""
        credential = _postgres_credential(password="  padded  ", username=" u ")
        assert normalize_create(_create(credential=credential)).credential is credential

    def test_a_value_that_is_only_whitespace_still_fails_validation(self) -> None:
        with pytest.raises(DataSourceValidationError):
            validate_create(normalize_create(_create(host="   ")))

    def test_update_strips_only_the_fields_it_carries(self) -> None:
        patch = normalize_update(DataSourceUpdate(host=" db2 ", port=5433))
        assert patch.host == "db2"
        assert patch.name is None  # absent stays absent: "leave unchanged"
        assert patch.port == 5433

    def test_update_does_not_touch_the_credential(self) -> None:
        credential = _postgres_credential(password=" spaced ")
        patch = normalize_update(DataSourceUpdate(credential=credential))
        assert patch.credential is credential

    def test_an_already_clean_value_is_unchanged(self) -> None:
        create = _create()
        assert normalize_create(create) == create


class TestCredentialDictRoundTrip:
    """``LocalEncryptedSecretStore.write``/``read`` and ``AzureKeyVaultSecretStore`` both
    serialise a ``Credential`` through these two functions before it ever reaches the
    ciphertext/Key Vault call -- the encryption and the network I/O are what the cookbook
    proves; this is the pure part that a database is not needed to check."""

    def test_round_trips_every_field(self) -> None:
        original = Credential(
            auth_method=AuthMethod.OAUTH,
            username="u",
            password="p",
            api_key="k",
            oauth_client_id="cid",
            oauth_client_secret="csecret",
        )
        restored = _credential_from_dict(_credential_to_dict(original))
        assert restored == original

    def test_auth_method_round_trips_as_the_enum_not_a_bare_string(self) -> None:
        data = _credential_to_dict(_postgres_credential())
        assert data["auth_method"] == "username_password"
        restored = _credential_from_dict(data)
        assert restored.auth_method is AuthMethod.USERNAME_PASSWORD


class TestMatchesTheWireContract:
    """``InternalSource``/``InternalSourceWrite`` (``api/openapi/schemas/configure.yaml``)
    predate #304 -- a stub-only resource #305's router will back for real. This module's
    enums are meant to mirror that schema's ``SourceType``/``AuthMethod``/``PermissionLevel``
    exactly (see the module docstring), so a hand-edit to either side that drifts from the
    other should fail here rather than surface as a confusing 422 once #305's router maps
    between them."""

    @staticmethod
    def _enum_values(name: str) -> set:
        schemas = yaml.safe_load(_CONFIGURE_SCHEMAS.read_text())
        return set(schemas[name]["enum"])

    def test_source_type_matches_the_wire_enum(self) -> None:
        assert {member.value for member in DataSourceType} == self._enum_values(
            "SourceType"
        )

    def test_auth_method_matches_the_wire_enum(self) -> None:
        assert {member.value for member in AuthMethod} == self._enum_values(
            "AuthMethod"
        )

    def test_permission_level_matches_the_wire_enum(self) -> None:
        assert {member.value for member in PermissionLevel} == self._enum_values(
            "PermissionLevel"
        )


class _RaisingDeleteSecretStore:
    """A ``SecretStore`` whose ``delete`` fails -- only ``delete`` is ever called on it."""

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.deleted: list = []

    def delete(self, secret_ref: str) -> None:
        self.deleted.append(secret_ref)
        raise self._error


class TestBestEffortSecretDelete:
    """Cleanup of a superseded or orphaned secret runs *after* the caller's real work (or while
    it is already handling a failure), so it must never turn a success into an error or
    replace the error being handled with its own. This is the one place that promise lives."""

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError("vault unreachable"),
            ConnectionError("connection reset"),
            TimeoutError("timed out"),
            KeyError("gone"),
            Exception("anything else"),
        ],
    )
    def test_a_failing_delete_is_swallowed_and_logged(
        self, error: Exception, caplog: pytest.LogCaptureFixture
    ) -> None:
        secrets = _RaisingDeleteSecretStore(error)
        with caplog.at_level(logging.WARNING, logger="egpt_retail.configure.store"):
            result = store_module.DataSourceStore._best_effort_delete_secret(
                secrets, "local:abc123"
            )

        assert result is None
        assert secrets.deleted == ["local:abc123"]  # it did try
        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "local:abc123" in record.getMessage()  # names what was orphaned
        assert record.exc_info is not None  # and keeps the traceback for the operator

    def test_the_log_carries_the_reference_and_never_the_error_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A reference string is not credential material, but the message is fixed text plus
        the ref -- nothing from the exception is interpolated into it."""
        secrets = _RaisingDeleteSecretStore(RuntimeError("password=hunter2 leaked"))
        with caplog.at_level(logging.WARNING, logger="egpt_retail.configure.store"):
            store_module.DataSourceStore._best_effort_delete_secret(secrets, "local:x")
        assert "hunter2" not in caplog.records[0].getMessage()

    def test_a_working_delete_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _Working:
            def __init__(self) -> None:
                self.deleted: list = []

            def delete(self, secret_ref: str) -> None:
                self.deleted.append(secret_ref)

        secrets = _Working()
        with caplog.at_level(logging.WARNING, logger="egpt_retail.configure.store"):
            store_module.DataSourceStore._best_effort_delete_secret(secrets, "local:ok")
        assert secrets.deleted == ["local:ok"]
        assert caplog.records == []

    @pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(1)])
    def test_an_interpreter_level_exit_is_not_swallowed(
        self, error: BaseException
    ) -> None:
        """Best-effort means ordinary failures only -- Ctrl-C and ``sys.exit`` still stop the
        process."""
        with pytest.raises(type(error)):
            store_module.DataSourceStore._best_effort_delete_secret(
                _RaisingDeleteSecretStore(error), "local:x"
            )
