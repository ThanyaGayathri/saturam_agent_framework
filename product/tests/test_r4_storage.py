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
