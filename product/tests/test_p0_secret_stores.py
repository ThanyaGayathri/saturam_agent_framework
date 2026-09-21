"""#304 -- backend selection and the Key Vault store, with no vault and no database.

``build_secret_store`` is the one call site that decides which backend a process gets, and
what it does is pure logic. ``AzureKeyVaultSecretStore`` needs no vault to test the part that is
ours: the Key Vault client is replaced by a small in-memory fake, so what is pinned here is the
wrapper's own behaviour -- the secret-name rules, the ``azure-key-vault:`` reference round trip,
how a missing secret is reported, and what ``delete`` does with a reference it does not own.
That the real ``SecretClient`` behaves as the fake does is not proved here (it is reviewed by
SDK shape only, as the PR says); a real vault is what would prove it.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from egpt_retail.app.settings import RetailSettings
from egpt_retail.configure import secrets as secrets_module
from egpt_retail.configure.secrets import (
    AzureKeyVaultSecretStore,
    LocalEncryptedSecretStore,
    SecretNotFoundError,
    build_secret_store,
)
from egpt_retail.contracts.data_source import AuthMethod, Credential

azure_core = pytest.importorskip(
    "azure.core.exceptions", reason="azure-core not installed"
)
pytest.importorskip(
    "azure.keyvault.secrets", reason="azure-keyvault-secrets not installed"
)
pytest.importorskip("azure.identity", reason="azure-identity not installed")
fernet = pytest.importorskip("cryptography.fernet", reason="cryptography not installed")

VAULT_URL = "https://retail-test.vault.azure.net"


def _credential(password: str = "hunter2") -> Credential:
    return Credential(
        auth_method=AuthMethod.USERNAME_PASSWORD, username="reader", password=password
    )


def _settings(**overrides: Any) -> RetailSettings:
    """Every secret-store setting given explicitly, so nothing is read from the environment."""
    fields: Dict[str, Any] = dict(
        retail_secret_store_provider="local_encrypted",
        retail_key_vault_url=None,
        retail_secret_encryption_key=None,
    )
    fields.update(overrides)
    return RetailSettings(**fields)


class _FakeSecretClient:
    """The slice of ``azure.keyvault.secrets.SecretClient`` the store uses."""

    instances: List["_FakeSecretClient"] = []

    def __init__(self, vault_url: str, credential: Any) -> None:
        self.vault_url = vault_url
        self.credential = credential
        self.values: Dict[str, Any] = {}
        self.calls: List[str] = []
        _FakeSecretClient.instances.append(self)

    def set_secret(self, name: str, value: str) -> None:
        self.calls.append("set")
        self.values[name] = value

    def get_secret(self, name: str) -> Any:
        self.calls.append("get")
        if name not in self.values:
            raise azure_core.ResourceNotFoundError(f"{name} not found")
        return SimpleNamespace(value=self.values[name])

    def begin_delete_secret(self, name: str) -> Any:
        self.calls.append("delete")
        if name not in self.values:
            raise azure_core.ResourceNotFoundError(f"{name} not found")
        del self.values[name]
        return SimpleNamespace(wait=lambda: None)


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> AzureKeyVaultSecretStore:
    import azure.identity
    import azure.keyvault.secrets

    _FakeSecretClient.instances = []
    monkeypatch.setattr(azure.keyvault.secrets, "SecretClient", _FakeSecretClient)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda: "ambient")
    return AzureKeyVaultSecretStore(vault_url=VAULT_URL)


def _client() -> _FakeSecretClient:
    return _FakeSecretClient.instances[-1]


class TestBuildSecretStore:
    def test_azure_without_a_vault_url_is_refused_and_says_which_setting(self) -> None:
        with pytest.raises(RuntimeError, match="EGPT_RETAIL_KEY_VAULT_URL"):
            build_secret_store(
                _settings(retail_secret_store_provider="azure_key_vault"), MagicMock()
            )

    def test_local_without_a_key_is_refused_and_says_which_setting(self) -> None:
        with pytest.raises(RuntimeError, match="EGPT_RETAIL_SECRET_ENCRYPTION_KEY"):
            build_secret_store(_settings(), MagicMock())

    def test_azure_with_a_vault_url_builds_the_key_vault_store(self, vault) -> None:
        store = build_secret_store(
            _settings(
                retail_secret_store_provider="azure_key_vault",
                retail_key_vault_url=VAULT_URL,
            ),
            MagicMock(),
        )
        assert isinstance(store, AzureKeyVaultSecretStore)
        assert _client().vault_url == VAULT_URL
        assert _client().credential == "ambient"  # the ambient identity, no shared key

    def test_local_with_a_key_builds_the_encrypted_store_on_the_given_connection(
        self,
    ) -> None:
        store = build_secret_store(
            _settings(
                retail_secret_encryption_key=fernet.Fernet.generate_key().decode()
            ),
            MagicMock(),
        )
        assert isinstance(store, LocalEncryptedSecretStore)

    def test_the_default_provider_is_the_local_store(self) -> None:
        """An unset provider must not silently reach for Key Vault (or anywhere else)."""
        assert RetailSettings().retail_secret_store_provider == "local_encrypted"

    def test_a_key_vault_url_is_ignored_by_the_local_provider(self) -> None:
        store = build_secret_store(
            _settings(
                retail_key_vault_url=VAULT_URL,
                retail_secret_encryption_key=fernet.Fernet.generate_key().decode(),
            ),
            MagicMock(),
        )
        assert isinstance(store, LocalEncryptedSecretStore)

    def test_a_malformed_encryption_key_is_refused_at_construction(self) -> None:
        with pytest.raises(RuntimeError, match="not a valid Fernet key"):
            build_secret_store(
                _settings(retail_secret_encryption_key="not-a-fernet-key"), MagicMock()
            )


class TestKeyVaultSecretName:
    """A Key Vault secret name is alphanumeric-and-dash only, at most 127 characters."""

    @pytest.mark.parametrize(
        "hint",
        [
            "Warehouse",
            "  Spaced  Out  ",
            "Prod (EU) / analytics_db!",
            "名前",
            "",
            "   ",
            "x" * 500,
            "a" * 40 + "---" + "b" * 40,
        ],
    )
    def test_the_name_is_always_a_valid_key_vault_name(self, hint: str) -> None:
        name = secrets_module._key_vault_secret_name(hint)
        assert re.fullmatch(r"[a-zA-Z0-9-]+", name), name
        assert len(name) <= 127

    def test_the_prefix_and_the_slug_identify_the_source_to_a_human(self) -> None:
        name = secrets_module._key_vault_secret_name("Prod Analytics")
        assert name.startswith("egpt-retail-datasource-prod-analytics-")

    def test_an_unusable_hint_falls_back_to_a_generic_slug(self) -> None:
        assert secrets_module._key_vault_secret_name("名前").startswith(
            "egpt-retail-datasource-source-"
        )

    def test_the_slug_is_capped(self) -> None:
        name = secrets_module._key_vault_secret_name("x" * 500)
        slug = name[len("egpt-retail-datasource-") :].rsplit("-", 1)[0]
        assert len(slug) <= 40

    def test_two_secrets_for_the_same_hint_never_collide(self) -> None:
        names = {secrets_module._key_vault_secret_name("Same") for _ in range(50)}
        assert len(names) == 50


class TestKeyVaultStore:
    def test_a_write_returns_a_reference_that_reads_back_the_exact_credential(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        ref = vault.write("Warehouse", _credential())
        assert ref.startswith("azure-key-vault:egpt-retail-datasource-warehouse-")
        assert vault.read(ref) == _credential()

    def test_the_reference_names_the_secret_that_was_stored(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        ref = vault.write("Warehouse", _credential())
        assert ref == "azure-key-vault:" + next(iter(_client().values))

    def test_the_stored_secret_is_the_credential_and_only_that(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        vault.write("Warehouse", _credential("s3cr3t"))
        (stored,) = _client().values.values()
        assert "s3cr3t" in stored  # it is the credential the vault holds ...
        assert "Warehouse" not in stored  # ... and nothing about which source it is

    def test_reading_an_unknown_secret_raises_secret_not_found(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        with pytest.raises(SecretNotFoundError):
            vault.read("azure-key-vault:egpt-retail-datasource-nope-000000000000")

    def test_reading_a_reference_this_store_did_not_issue_never_reaches_the_vault(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        with pytest.raises(SecretNotFoundError):
            vault.read("local:abc123")
        assert _client().calls == []

    def test_a_secret_with_no_value_is_reported_as_not_found(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        """Something else in the vault can set a secret with a null value; this store never
        writes one, and must not try to parse it."""
        _client().values["egpt-retail-datasource-x-000000000000"] = None
        with pytest.raises(SecretNotFoundError):
            vault.read("azure-key-vault:egpt-retail-datasource-x-000000000000")

    def test_delete_removes_the_secret(self, vault: AzureKeyVaultSecretStore) -> None:
        ref = vault.write("Warehouse", _credential())
        vault.delete(ref)
        with pytest.raises(SecretNotFoundError):
            vault.read(ref)

    def test_deleting_an_already_deleted_secret_is_a_no_op(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        """Callers delete best-effort after a rotation or a source delete; a second delete
        racing the first must not raise."""
        ref = vault.write("Warehouse", _credential())
        vault.delete(ref)
        vault.delete(ref)

    def test_deleting_a_reference_this_store_did_not_issue_is_a_no_op_that_never_reaches_the_vault(
        self, vault: AzureKeyVaultSecretStore
    ) -> None:
        vault.delete("local:abc123")
        assert _client().calls == []

    def test_async_twins(self, vault: AzureKeyVaultSecretStore) -> None:
        async def scenario() -> None:
            ref = await vault.awrite("Warehouse", _credential("async"))
            assert (await vault.aread(ref)).password == "async"
            await vault.adelete(ref)
            await vault.adelete(ref)  # no-op, not an error
            with pytest.raises(SecretNotFoundError):
                await vault.aread(ref)

        asyncio.run(scenario())
