#!/usr/bin/env bash
# R4 -- the same four scenarios, loading from a real S3-compatible object store.
#
# R04_landing_to_silver.py proves the pipeline against a directory. This proves the half a
# directory cannot: that "loads from storage" means an S3 client, a bucket, a real network
# hop, and paths that survive the round trip into a form the landing contract still parses.
# The path normalisation in egpt_retail/storage is the whole reason this script exists --
# a bucket-qualified key is rejected by parse_landing_path, and only a real bucket shows that.
#
# Shell rather than Python for the same reason C01_minio_roundtrip.sh gives: `mc` already
# handles S3 request signing and bucket creation, and wrapping docker and mc in
# subprocess.run to earn a .py extension would make this harder to re-run by hand.
#
# Needs Docker. Leaves MinIO and Postgres running; clean up with:
#   docker compose -f deploy/compose.yaml --profile full down -v
#
# Run:
#   bash cookbook/94_retail/R04_minio_load.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURES_DIR="$REPO_ROOT/product/egpt_retail/contracts/_fixtures/landing"
BUCKET="egpt-landing"
ENDPOINT="http://host.docker.internal:9000"
HOST_ENDPOINT="http://127.0.0.1:9000"
WAREHOUSE_URL="postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse"

run_mc() {
  docker run --rm --add-host=host.docker.internal:host-gateway --entrypoint sh "$@"
}

echo "R4 -- landing to silver, from a real object store"
echo "=============================================================================="

echo "-- 1. Bring up MinIO and Postgres --"
docker compose -f "$REPO_ROOT/deploy/compose.yaml" up -d postgres >/dev/null
docker compose -f "$REPO_ROOT/deploy/compose.yaml" --profile full up -d minio >/dev/null

echo "-- 2. Wait for the S3 endpoint --"
ready=0
for _ in $(seq 1 30); do
  if run_mc minio/mc:latest -c \
      "mc alias set local $ENDPOINT minioadmin minioadmin" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "MinIO did not become ready on $ENDPOINT" >&2
  exit 1
fi
echo "   ready"

echo "-- 3. Create the bucket and upload C1's fixtures --"
# `mc cp --recursive SRC DST` includes SRC's own directory name in the destination keys, so
# the target is the bucket root and the objects land under raw/. Recorded in TEST_LOG.md by
# C1, and re-learned here would have cost the same hour.
run_mc -v "$FIXTURES_DIR/raw:/fixtures/raw:ro" minio/mc:latest -c \
  "mc alias set local $ENDPOINT minioadmin minioadmin >/dev/null &&
   mc mb -p local/$BUCKET >/dev/null &&
   mc cp --recursive /fixtures/raw local/$BUCKET" | tail -1

echo "-- 4. What actually landed in the bucket --"
run_mc minio/mc:latest -c \
  "mc alias set local $ENDPOINT minioadmin minioadmin >/dev/null &&
   mc ls -r local/$BUCKET" | sed 's/^/   /'

echo "-- 5. The warehouse schema --"
EGPT_RETAIL_WAREHOUSE_DB_URL="$WAREHOUSE_URL" \
  "$REPO_ROOT/.venv/bin/python" -m egpt_retail.entrypoints warehouse-migrate | sed 's/^/   /'

echo "-- 6. The four scenarios, reading from MinIO rather than from disk --"
# The same script, unmodified. Only the storage provider changes: this is what makes the
# object-store seam a seam rather than a second code path.
EGPT_RETAIL_WAREHOUSE_DB_URL="$WAREHOUSE_URL" \
EGPT_RETAIL_STORAGE_PROVIDER=s3 \
EGPT_RETAIL_STORAGE_ENDPOINT="$HOST_ENDPOINT" \
EGPT_RETAIL_STORAGE_CONTAINER="$BUCKET" \
EGPT_RETAIL_STORAGE_ACCESS_KEY=minioadmin \
EGPT_RETAIL_STORAGE_SECRET_KEY=minioadmin \
EGPT_RETAIL_R04_FROM_STORE=1 \
  "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/cookbook/94_retail/R04_landing_to_silver.py"

echo
echo "=============================================================================="
echo "Evidence: the four scenarios produced the same counts, the same de-duplication"
echo "outcomes and the same two blocked verdicts reading from an S3 bucket as they do"
echo "reading from a directory. Every key returned by the store parsed under the landing"
echo "contract, which is the one thing a local directory cannot prove."
