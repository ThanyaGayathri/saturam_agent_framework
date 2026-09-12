"""AZ-A -- Azure Blob storage backend, against a real Azurite.

The object-store seam already has two backends (`LocalDirectoryStore`, `S3ObjectStore`) and
one established test pattern (`product/tests/test_r4_storage.py`). This is that same proof
for the third: `AzureBlobObjectStore`, run against Azurite -- the official local emulator for
Azure Storage, the direct analogue of MinIO for the S3 backend's own verification
(`cookbook/94_retail/R04_minio_load.sh`).

What it proves:
  1. C1's committed landing fixtures, uploaded into a fresh Azurite container, list and read
     back byte-for-byte identically to `LocalDirectoryStore` reading the same fixtures off
     disk.
  2. A path that tries to escape the landing root is refused, the same `ValueError` shape
     `LocalDirectoryStore._resolve` raises.

Needs Docker and Azurite:
    docker run -d --name egpt-azurite -p 10000:10000 -p 10001:10001 -p 10002:10002 \\
      mcr.microsoft.com/azure-storage/azurite:latest \\
      azurite --blobHost 0.0.0.0 --queueHost 0.0.0.0 --tableHost 0.0.0.0

Run:
    .venv/bin/python cookbook/95_azure_migration/AZ_A_blob_roundtrip.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

from azure.core.exceptions import ResourceExistsError  # noqa: E402
from azure.storage.blob import BlobServiceClient  # noqa: E402
from egpt_retail.storage.azure_blob import AzureBlobObjectStore  # noqa: E402
from egpt_retail.storage.local import LocalDirectoryStore  # noqa: E402

# Azurite's well-known development account. Not a secret -- it's published in Microsoft's own
# Azurite docs and is the same value every local Azurite instance accepts.
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
    "/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
CONTAINER = "egpt-landing"
FIXTURES = REPO_ROOT / "product" / "egpt_retail" / "contracts" / "_fixtures" / "landing"


def upload_fixtures(connection_string: str, container: str, fixtures_root: Path) -> int:
    """Uploads every file under fixtures_root/raw, preserving its raw/... blob name."""
    client = BlobServiceClient.from_connection_string(connection_string)
    container_client = client.get_container_client(container)
    try:
        container_client.create_container()
    except ResourceExistsError:
        # Re-runnable: wipe and re-upload rather than fail on a container left over from a
        # previous run of this same script.
        for blob in container_client.list_blobs():
            container_client.delete_blob(blob.name)

    uploaded = 0
    for path in sorted((fixtures_root / "raw").rglob("*.ndjson")):
        blob_name = path.relative_to(fixtures_root).as_posix()
        with path.open("rb") as fh:
            container_client.upload_blob(blob_name, fh, overwrite=True)
        uploaded += 1
    return uploaded


def main() -> int:
    print("AZ-A -- Azure Blob storage backend, against Azurite")
    print("=" * 78)

    print("-- 1. Upload C1's fixtures into a fresh Azurite container --")
    try:
        uploaded = upload_fixtures(AZURITE_CONNECTION_STRING, CONTAINER, FIXTURES)
    except Exception as exc:  # noqa: BLE001 -- top-level script, report and fail cleanly
        print(f"FAIL: could not reach Azurite or upload fixtures: {exc!r}")
        return 1
    print(f"   uploaded {uploaded} fixture files")

    print("-- 2. List and read through AzureBlobObjectStore --")
    azure_store = AzureBlobObjectStore(
        container=CONTAINER, connection_string=AZURITE_CONNECTION_STRING
    )
    local_store = LocalDirectoryStore(FIXTURES)

    azure_paths = azure_store.list_objects()
    local_paths = local_store.list_objects()
    print(f"   Azure Blob listed {len(azure_paths)} objects")
    print(f"   local directory listed {len(local_paths)} objects")

    if azure_paths != local_paths:
        print("FAIL: listings differ")
        print(f"   azure: {azure_paths}")
        print(f"   local: {local_paths}")
        return 1

    for path in azure_paths:
        azure_bytes = azure_store.get_bytes(path)
        local_bytes = local_store.get_bytes(path)
        if azure_bytes != local_bytes:
            print(f"FAIL: {path} differs between Azure Blob and local")
            return 1
    print(
        f"   all {len(azure_paths)} objects byte-for-byte identical to LocalDirectoryStore"
    )

    print("-- 3. Async variants agree with their sync twins --")
    import asyncio

    async def _check_async() -> bool:
        listed = await azure_store.alist_objects()
        if listed != azure_store.list_objects():
            return False
        first = listed[0]
        return await azure_store.aget_bytes(first) == azure_store.get_bytes(first)

    if not asyncio.run(_check_async()):
        print("FAIL: async variants disagree with their sync twins")
        return 1
    print("   alist_objects/aget_bytes agree with list_objects/get_bytes")

    print("-- 4. A path escaping the landing root is refused --")
    try:
        azure_store.get_bytes("raw/../../../etc/passwd")
        print("FAIL: escaping path was not refused")
        return 1
    except ValueError as exc:
        print(f"   refused as expected: {exc}")

    print("-- 5. A half-configured credential pair is refused at construction --")
    try:
        AzureBlobObjectStore(
            container=CONTAINER,
            account_url="http://127.0.0.1:10000/devstoreaccount1",
            account_name="onlyname",
        )
        print("FAIL: half-configured credentials were accepted")
        return 1
    except RuntimeError as exc:
        print(f"   refused as expected: {exc}")

    print()
    print("=" * 78)
    print("PASS -- Azure Blob listing and get_bytes match LocalDirectoryStore's output")
    print(
        "byte-for-byte on the same fixtures. The escaping path was refused with the same"
    )
    print(
        "ValueError shape LocalDirectoryStore uses. The half-configured credential pair"
    )
    print("was refused at construction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
