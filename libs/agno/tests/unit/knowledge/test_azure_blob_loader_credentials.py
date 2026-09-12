"""The Azure Blob loader's client builders: what reaches ``ClientSecretCredential``.

``test_remote_content_config.py`` covers ``AzureBlobConfig._validate_auth``, the construction-time
invariant. These cover the two methods that *consume* it -- ``_get_azure_blob_client`` and its
async twin -- which previously had no direct test: a field-swap or a dropped narrowing check
would not have turned anything red. The Azure SDK is stubbed through ``sys.modules`` so the
tests need neither ``azure-storage-blob`` nor ``azure-identity`` installed, and they never
exercise the network.
"""

import asyncio
import sys
import types
from typing import Any, Dict, List

import pytest

# The public entry point first: importing the loader submodule directly hits a pre-existing
# cycle (loaders/__init__ -> azure_blob -> base -> remote_knowledge -> azure_blob) that
# `egpt.knowledge` resolves by importing in the order the package expects.
import egpt.knowledge  # noqa: F401
from egpt.integrations.knowledge.loaders.azure_blob import AzureBlobLoader
from egpt.integrations.knowledge.remote_content import AzureBlobConfig
from egpt.integrations.knowledge.remote_content.remote_content import AzureBlobContent
from egpt.knowledge.content import Content


class _Recorder:
    """A stand-in class that records the kwargs it was constructed with."""

    calls: List[Dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).calls.append(kwargs)


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture
def azure_stubs(monkeypatch):
    """Fake ``azure.storage.blob[.aio]`` and ``azure.identity[.aio]`` with recording classes."""
    stubs = {}
    for name in ("sync_blob", "aio_blob", "sync_cred", "aio_cred"):
        stubs[name] = type(f"Stub_{name}", (_Recorder,), {"calls": []})
    azure = _module("azure")
    storage = _module("azure.storage")
    blob = _module("azure.storage.blob", BlobServiceClient=stubs["sync_blob"])
    blob_aio = _module("azure.storage.blob.aio", BlobServiceClient=stubs["aio_blob"])
    identity = _module("azure.identity", ClientSecretCredential=stubs["sync_cred"])
    identity_aio = _module("azure.identity.aio", ClientSecretCredential=stubs["aio_cred"])
    for mod in (azure, storage, blob, blob_aio, identity, identity_aio):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    return stubs


def _sp_config() -> AzureBlobConfig:
    return AzureBlobConfig(
        id="azure-source",
        name="My Azure Storage",
        tenant_id="tenant-123",
        client_id="client-456",
        client_secret="secret-789",
        storage_account="mystorageaccount",
        container="mycontainer",
    )


def test_sync_builder_passes_the_three_service_principal_fields_through(azure_stubs):
    AzureBlobLoader()._get_azure_blob_client(_sp_config())

    assert azure_stubs["sync_cred"].calls == [
        {"tenant_id": "tenant-123", "client_id": "client-456", "client_secret": "secret-789"}
    ]
    (blob_call,) = azure_stubs["sync_blob"].calls
    assert blob_call["account_url"] == "https://mystorageaccount.blob.core.windows.net"
    assert isinstance(blob_call["credential"], azure_stubs["sync_cred"])
    assert azure_stubs["aio_cred"].calls == [], "the sync builder must not touch azure.identity.aio"


def test_async_builder_passes_the_three_service_principal_fields_through(azure_stubs):
    AzureBlobLoader()._get_azure_blob_client_async(_sp_config())

    assert azure_stubs["aio_cred"].calls == [
        {"tenant_id": "tenant-123", "client_id": "client-456", "client_secret": "secret-789"}
    ]
    (blob_call,) = azure_stubs["aio_blob"].calls
    assert blob_call["account_url"] == "https://mystorageaccount.blob.core.windows.net"
    assert isinstance(blob_call["credential"], azure_stubs["aio_cred"])
    assert azure_stubs["sync_cred"].calls == [], "the async builder must not touch azure.identity"


@pytest.mark.parametrize("builder", ["_get_azure_blob_client", "_get_azure_blob_client_async"])
def test_sas_token_path_never_imports_azure_identity(azure_stubs, monkeypatch, builder):
    # A None entry in sys.modules makes the import raise ImportError -- proving the SAS path
    # does not go near azure.identity at all, rather than merely not calling it.
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    monkeypatch.setitem(sys.modules, "azure.identity.aio", None)
    config = AzureBlobConfig(
        id="azure-sas", name="sas", sas_token="sv=2024&sig=abc", storage_account="acct", container="c"
    )

    getattr(AzureBlobLoader(), builder)(config)

    stub = azure_stubs["sync_blob"] if builder == "_get_azure_blob_client" else azure_stubs["aio_blob"]
    (blob_call,) = stub.calls
    assert blob_call["credential"] == "sv=2024&sig=abc"


@pytest.mark.parametrize("builder", ["_get_azure_blob_client", "_get_azure_blob_client_async"])
@pytest.mark.parametrize("field", ["tenant_id", "client_id", "client_secret"])
def test_field_reset_after_validation_raises_value_error_naming_the_field(azure_stubs, builder, field):
    # BaseStorageConfig is neither frozen nor validate_assignment, so the pydantic validator
    # cannot see this. The builder must refuse with a ValueError -- not an AssertionError, which
    # `python -O` compiles away -- and must name the field, never a credential value.
    config = _sp_config()
    setattr(config, field, None)

    with pytest.raises(ValueError) as excinfo:
        getattr(AzureBlobLoader(), builder)(config)

    assert not isinstance(excinfo.value, AssertionError)
    message = str(excinfo.value)
    assert field in message and "azure-source" in message
    for secret in ("tenant-123", "client-456", "secret-789"):
        assert secret not in message
    assert azure_stubs["sync_cred"].calls == [] and azure_stubs["aio_cred"].calls == []


def _incomplete_content():
    return Content(name="doc", remote_content=AzureBlobContent(config_id="azure-source", blob_name="a.txt"))


def test_sync_loader_logs_and_returns_on_an_incomplete_config(azure_stubs, monkeypatch):
    logged: List[str] = []
    monkeypatch.setattr("egpt.integrations.knowledge.loaders.azure_blob.log_error", logged.append)
    config = _sp_config()
    config.client_secret = None

    result = AzureBlobLoader()._load_from_azure_blob(
        _incomplete_content(), upsert=False, skip_if_exists=False, config=config
    )

    assert result is None
    assert len(logged) == 1 and logged[0].startswith("Error creating Azure Blob client:")
    assert "client_secret" in logged[0] and "secret-789" not in logged[0]
    assert azure_stubs["sync_blob"].calls == [], "no client is built from an incomplete config"


def test_async_loader_logs_and_returns_on_an_incomplete_config(azure_stubs, monkeypatch):
    logged: List[str] = []
    monkeypatch.setattr("egpt.integrations.knowledge.loaders.azure_blob.log_error", logged.append)
    config = _sp_config()
    config.tenant_id = None

    result = asyncio.run(
        AzureBlobLoader()._aload_from_azure_blob(
            _incomplete_content(), upsert=False, skip_if_exists=False, config=config
        )
    )

    assert result is None
    assert len(logged) == 1 and logged[0].startswith("Error creating Azure Blob client:")
    assert "tenant_id" in logged[0] and "tenant-123" not in logged[0]
    assert azure_stubs["aio_blob"].calls == [], "no client is built from an incomplete config"
