"""#304 -- the parts of the data-source contract and secret encoding that need no database.

The round trip against real Postgres (credential storage, rotation, deletion) is proved by
``cookbook/94_retail/P0_data_source_registration.py``, the same split every other store in
this product uses (see ``semantic/proposals.py``'s own note on why). What is worth testing
here is validation logic a wrong change would break silently, and the one property the whole
design rests on: a credential is never accidentally printable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from egpt_retail.configure.secrets import _credential_from_dict, _credential_to_dict
from egpt_retail.contracts.data_source import (
    AuthMethod,
    Credential,
    DataSourceCreate,
    DataSourceType,
    DataSourceValidationError,
    PermissionLevel,
    validate_create,
    validate_credential,
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
