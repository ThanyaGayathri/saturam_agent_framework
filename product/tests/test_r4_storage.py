"""R4 without a warehouse -- the object-store seam against C1's committed fixtures.

This is the test that proves R4 can read what Lane C writes, and it needs no database, no
Docker and no credentials: the fixtures are in the repository and the local backend reads
them. Every path the store returns is fed to the landing contract's own parser, because a
path R4 can list but not parse is the exact failure the seam exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.landing import SourceName, parse_landing_path
from egpt_retail.storage import LocalDirectoryStore, build_object_store

_FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "_fixtures"
    / "landing"
)


@pytest.fixture(scope="module")
def store() -> LocalDirectoryStore:
    return LocalDirectoryStore(_FIXTURES)


def test_lists_exactly_the_four_committed_fixture_files(
    store: LocalDirectoryStore,
) -> None:
    paths = store.list_objects()
    assert len(paths) == 4, paths
    assert sum(1 for p in paths if "/crm/" in p) == 2, "two CRM pulls, one overlapping"


def test_every_listed_path_parses_under_the_landing_contract(
    store: LocalDirectoryStore,
) -> None:
    """The seam's whole job. A path that lists but does not parse is a loader that stops.

    parse_landing_path anchors on '^raw/', so a bucket-qualified or absolute path is refused
    outright -- which is why the store returns landing-root-relative paths and nothing else.
    """
    for path in store.list_objects():
        parts = parse_landing_path(path)
        assert isinstance(parts.source, SourceName)
        assert parts.ingested_at.tzinfo is not None


def test_listing_is_sorted_so_the_fold_order_is_the_contract_s(
    store: LocalDirectoryStore,
) -> None:
    """Oldest ingested_at first, within a source and partition.

    De-duplication resolves an exact watermark tie to the earliest-pulled file, so the order
    files are folded in is load-bearing rather than cosmetic. The two CRM pulls are the case
    that matters: 020000Z must be folded before 060000Z.
    """
    paths = list(store.list_objects())
    assert paths == sorted(paths)
    crm = [p for p in paths if "/crm/" in p]
    assert [parse_landing_path(p).ingested_at for p in crm] == sorted(
        parse_landing_path(p).ingested_at for p in crm
    )


def test_returned_paths_are_relative_and_start_at_raw(
    store: LocalDirectoryStore,
) -> None:
    for path in store.list_objects():
        assert path.startswith("raw/")
        assert not path.startswith("/")
        assert str(_FIXTURES) not in path


def test_get_bytes_reads_ndjson_lines(store: LocalDirectoryStore) -> None:
    path = next(p for p in store.list_objects() if "/crm/" in p)
    lines = [
        ln for ln in store.get_bytes(path).decode("utf-8").splitlines() if ln.strip()
    ]
    assert lines and all(ln.lstrip().startswith("{") for ln in lines)


@pytest.mark.anyio
async def test_async_variants_agree_with_their_sync_twins() -> None:
    """Both variants exist and return the same thing, per the repository's standing rule."""
    store = LocalDirectoryStore(_FIXTURES)
    listed = await store.alist_objects()
    assert listed == store.list_objects()
    first = listed[0]
    assert await store.aget_bytes(first) == store.get_bytes(first)


def test_a_path_escaping_the_landing_root_is_refused(
    store: LocalDirectoryStore,
) -> None:
    """Landed paths come from object storage, which is outside this process."""
    with pytest.raises(ValueError):
        store.get_bytes("raw/../../../etc/passwd")


#: Every shape the seam refuses, and why each is not merely a ``..`` in disguise. The point of
#: the list is that no backend may quietly accept one of these and turn it into a key.
_REFUSED_PATHS = (
    "/etc/passwd",  # absolute -- silently became the key 'prefix//etc/passwd'
    "raw\\..\\..\\etc\\passwd",  # Windows-flavoured traversal, no '/' segments at all
    "C:/Windows/win.ini",  # a drive letter is not a landing-root-relative path
    "raw/../../../etc/passwd",  # the one shape the '..'-only check already caught
    "..",  # a bare '..' with no prefix
    "raw/./crm",  # '.' collapses on a filesystem, is a real segment in a blob name
    "raw//crm",  # '//' likewise: one key on Azure Blob and S3, collapsed locally
    "raw/crm\x00.ndjson",  # a NUL byte: refused by the allowlist, not by any named rule
    "raw/crm /x.ndjson",  # whitespace, which no landed filename ever contains
)


@pytest.mark.parametrize("path", _REFUSED_PATHS)
def test_every_backend_refuses_the_same_non_relative_paths(path: str) -> None:
    """base.py's stated contract: the backends are indistinguishable to their caller.

    Before the shared ``validate_object_path``, only ``..`` was checked, and only by the local
    and Azure Blob backends -- so ``/etc/passwd`` was a ``ValueError`` on one and a different,
    unvalidated key on the other two. A flat key space makes that unexploitable rather than
    correct: the caller cannot tell which backend it is talking to except by which inputs blow
    up, which is exactly what the seam exists to hide.
    """
    from egpt_retail.storage.azure_blob import AzureBlobObjectStore
    from egpt_retail.storage.s3 import S3ObjectStore

    stores = (
        LocalDirectoryStore(_FIXTURES),
        S3ObjectStore(
            container="c", endpoint_url="http://127.0.0.1:9000", prefix="landing"
        ),
        # A shared key rather than the ambient chain: the refusal must happen before any
        # client or credential is built, and DefaultAzureCredential probes its environment.
        AzureBlobObjectStore(
            container="c",
            account_url="http://127.0.0.1:10000/devstoreaccount1",
            account_name="devstoreaccount1",
            account_key="not-a-real-key",
            prefix="landing",
        ),
    )
    for store in stores:
        with pytest.raises(ValueError):
            store.get_bytes(path)


def test_an_absolute_path_is_refused_as_absolute_not_as_a_stray_segment() -> None:
    """The reviewer's named case, held to its own message.

    Refusal alone is over-determined: an absolute path's leading empty segment already trips
    the ``//`` rule, so deleting the ``startswith('/')`` branch changes no test's pass/fail.
    It exists for the message a human reads at 3am, and that is what this pins.
    """
    from egpt_retail.storage.base import validate_object_path

    with pytest.raises(ValueError) as excinfo:
        validate_object_path("/etc/passwd", "root")
    assert "is absolute" in str(excinfo.value)


def test_every_backend_still_accepts_a_real_landed_path_and_the_raw_prefix() -> None:
    """The allowlist must not refuse what the landing contract actually produces."""
    from egpt_retail.storage.azure_blob import AzureBlobObjectStore
    from egpt_retail.storage.base import RAW_PREFIX, validate_object_path
    from egpt_retail.storage.s3 import S3ObjectStore

    landed = next(p for p in LocalDirectoryStore(_FIXTURES).list_objects())
    # A real contract path, not merely a string that happens to pass the allowlist.
    parse_landing_path(landed)
    for candidate in (landed, RAW_PREFIX, "raw", ""):
        assert validate_object_path(candidate, "root") == candidate

    s3 = S3ObjectStore(container="c", endpoint_url="http://127.0.0.1:9000", prefix="p")
    azure = AzureBlobObjectStore(
        container="c",
        account_url="http://127.0.0.1:10000/devstoreaccount1",
        prefix="p",
    )
    assert s3._to_key(landed) == f"p/{landed}"
    assert azure._to_key(landed) == f"p/{landed}"
    assert LocalDirectoryStore(_FIXTURES).get_bytes(landed)


def test_missing_prefix_lists_nothing_rather_than_raising(tmp_path: Path) -> None:
    """An empty landing root is a real state -- a deployment connected before a first pull."""
    assert LocalDirectoryStore(tmp_path).list_objects() == ()


def test_build_object_store_refuses_an_unset_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence here would mean loading nothing and reporting a clean run."""
    monkeypatch.delenv("EGPT_RETAIL_STORAGE_PROVIDER", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        build_object_store(RetailSettings())
    assert "EGPT_RETAIL_STORAGE_PROVIDER" in str(excinfo.value)


def test_build_object_store_refuses_local_without_a_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EGPT_RETAIL_STORAGE_PROVIDER", "local")
    monkeypatch.delenv("EGPT_RETAIL_STORAGE_LOCAL_ROOT", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        build_object_store(RetailSettings())
    assert "EGPT_RETAIL_STORAGE_LOCAL_ROOT" in str(excinfo.value)


def test_build_object_store_builds_a_local_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EGPT_RETAIL_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("EGPT_RETAIL_STORAGE_LOCAL_ROOT", str(_FIXTURES))
    built = build_object_store(RetailSettings())
    assert isinstance(built, LocalDirectoryStore)
    assert len(built.list_objects()) == 4


@pytest.mark.parametrize(
    "access_key,secret_key",
    [("only-access", None), (None, "only-secret")],
)
def test_half_configured_s3_credentials_are_refused(access_key, secret_key) -> None:
    """A Helm secret with one key mistyped must fail, not silently use ambient credentials.

    Both keys set means explicit credentials; neither means the SDK's own chain, which on AKS
    is workload identity and is the intended path. Exactly one is always a typo, and falling
    through to a different identity than the one configured is the kind of quiet substitution
    that is found much later, by someone wondering why a bucket is empty.
    """
    from egpt_retail.storage.s3 import S3ObjectStore

    with pytest.raises(RuntimeError) as excinfo:
        S3ObjectStore(
            container="egpt-landing",
            endpoint_url="http://127.0.0.1:9000",
            access_key=access_key,
            secret_key=secret_key,
        )
    assert "EGPT_RETAIL_STORAGE_" in str(excinfo.value)


def test_both_or_neither_s3_credentials_are_accepted() -> None:
    """The two legitimate configurations still construct."""
    from egpt_retail.storage.s3 import S3ObjectStore

    S3ObjectStore(container="c", endpoint_url="http://127.0.0.1:9000")
    S3ObjectStore(
        container="c",
        endpoint_url="http://127.0.0.1:9000",
        access_key="a",
        secret_key="b",
    )


@pytest.mark.parametrize(
    "account_name,account_key",
    [("only-name", None), (None, "only-key")],
)
def test_half_configured_azure_blob_credentials_are_refused(
    account_name, account_key
) -> None:
    """The Azure analogue of the S3 access-key-pair test above.

    Both keys set means an explicit shared key; neither means the ambient credential chain
    (DefaultAzureCredential), which on Container Apps is managed identity and is the intended
    path. Exactly one is always a typo, and falling through to a different identity than the
    one configured is the same quiet substitution the S3 backend refuses.
    """
    from egpt_retail.storage.azure_blob import AzureBlobObjectStore

    with pytest.raises(RuntimeError) as excinfo:
        AzureBlobObjectStore(
            container="egpt-landing",
            account_url="http://127.0.0.1:10000/devstoreaccount1",
            account_name=account_name,
            account_key=account_key,
        )
    assert "EGPT_RETAIL_STORAGE_" in str(excinfo.value)


def test_both_or_neither_azure_blob_credentials_are_accepted() -> None:
    """The two legitimate shared-key configurations still construct."""
    from egpt_retail.storage.azure_blob import AzureBlobObjectStore

    AzureBlobObjectStore(
        container="c", account_url="http://127.0.0.1:10000/devstoreaccount1"
    )
    AzureBlobObjectStore(
        container="c",
        account_url="http://127.0.0.1:10000/devstoreaccount1",
        account_name="a",
        account_key="b",
    )


def test_azure_blob_with_a_connection_string_needs_no_account_url() -> None:
    """A connection string already names the account; it is not a fourth required field."""
    from egpt_retail.storage.azure_blob import AzureBlobObjectStore

    AzureBlobObjectStore(
        container="c",
        connection_string="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
        "/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;",
    )


def test_azure_blob_refuses_when_no_account_is_addressable() -> None:
    """No URL, no name, no connection string: nothing tells the SDK which account to use."""
    from egpt_retail.storage.azure_blob import AzureBlobObjectStore

    with pytest.raises(RuntimeError) as excinfo:
        AzureBlobObjectStore(container="c")
    assert "EGPT_RETAIL_STORAGE_ACCOUNT_URL" in str(excinfo.value)


def test_azure_blob_refuses_a_path_escaping_the_landing_root() -> None:
    """Mirrors LocalDirectoryStore's containment check, defence in depth for a flat key space."""
    from egpt_retail.storage.azure_blob import AzureBlobObjectStore

    store = AzureBlobObjectStore(
        container="c", account_url="http://127.0.0.1:10000/devstoreaccount1"
    )
    with pytest.raises(ValueError):
        store.get_bytes("raw/../../../etc/passwd")


def test_build_object_store_refuses_an_unrecognized_provider() -> None:
    """The latent bug this package fixes: an unrecognized provider must raise, not become S3.

    `StorageProvider`'s own `Literal` already refuses most typos before a real settings
    object can even be built from the environment -- this constructs a valid settings object
    and then reaches past that validation (the way a stale enum value from a rolling deploy,
    or a caller that builds ``RetailSettings`` some other way, still could) to prove
    `build_object_store` itself no longer falls through to S3 for anything unrecognized.
    """
    settings = RetailSettings()
    settings.retail_storage_provider = "not_a_real_provider"  # type: ignore[assignment]
    with pytest.raises(ValueError) as excinfo:
        build_object_store(settings)
    assert "not_a_real_provider" in str(excinfo.value)
