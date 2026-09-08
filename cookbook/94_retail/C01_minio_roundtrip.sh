#!/usr/bin/env bash
# C1 -- the landing contract's fixtures, round-tripped through a real S3-compatible store.
#
# The delivery plan's verify step says "upload it". C01_landing_contract.py proves the
# contract's path, format and row-schema logic against fixture files on local disk; this
# script closes the one gap that leaves open -- that a conforming file survives an actual
# upload to and download from object storage unchanged. MinIO stands in for Azure ADLS Gen2
# (D4) locally, the same role it plays in R2's kind-cluster local-infra chart, reusing that
# chart's `egpt-landing` bucket name.
#
# Shell on purpose, matching R02_cluster_evidence.sh's own reasoning: wrapping `docker` and
# `mc` in `subprocess.run` to satisfy a `.py` extension would make this harder to re-run by
# hand, for no benefit -- `mc` already handles S3 request signing, which a hand-rolled HTTP
# client would only reimplement.
#
# Needs Docker. Brings MinIO up via deploy/compose.yaml's "full" profile and uses the
# official `minio/mc` image for every S3 operation -- no new Python dependency.
#
# Run:
#     bash cookbook/94_retail/C01_minio_roundtrip.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURES_DIR="$REPO_ROOT/product/egpt_retail/contracts/_fixtures/landing"
BUCKET="egpt-landing"
ENDPOINT="http://host.docker.internal:9000"
DOWNLOAD_DIR="$(mktemp -d)"
trap 'rm -rf "$DOWNLOAD_DIR"' EXIT

run_mc() {
  docker run --rm --add-host=host.docker.internal:host-gateway --entrypoint sh "$@"
}

echo "Landing contract -- C1 / D4, round-tripped through MinIO"
echo "=============================================================================="

echo
echo "-- starting MinIO (deploy/compose.yaml, profile 'full') --"
docker compose -f "$REPO_ROOT/deploy/compose.yaml" --profile full up -d minio

echo
echo "-- waiting for MinIO to accept S3 requests --"
ready=0
for _ in $(seq 1 30); do
  if run_mc minio/mc -c "mc alias set local $ENDPOINT minioadmin minioadmin" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "MinIO did not become reachable at $ENDPOINT in time" >&2
  exit 1
fi

echo
echo "-- uploading the hand-written fixtures, preserving landing_path()'s layout --"
run_mc -v "$FIXTURES_DIR/raw:/fixtures/raw:ro" minio/mc -c "
  mc alias set local $ENDPOINT minioadmin minioadmin &&
  mc mb -p local/$BUCKET &&
  mc cp --recursive /fixtures/raw local/$BUCKET
"

echo
echo "-- what actually landed, listed straight from the bucket --"
run_mc minio/mc -c "
  mc alias set local $ENDPOINT minioadmin minioadmin >/dev/null &&
  mc ls -r local/$BUCKET
"

echo
echo "-- downloading it back --"
mkdir -p "$DOWNLOAD_DIR/raw"
run_mc -v "$DOWNLOAD_DIR:/download" minio/mc -c "
  mc alias set local $ENDPOINT minioadmin minioadmin &&
  mc cp --recursive local/$BUCKET/raw /download
"

echo
echo "-- re-running C01_landing_contract.py's own verification against the round-tripped copy --"
EGPT_LANDING_FIXTURES_ROOT="$DOWNLOAD_DIR" "$REPO_ROOT/.venv/bin/python" \
  "$REPO_ROOT/cookbook/94_retail/C01_landing_contract.py"

echo
echo "Evidence: every fixture survived a real upload to and download from an S3-compatible"
echo "store -- same paths, same rows, same defect, same dedup result -- which is what"
echo "'upload it' in the verify step actually requires, not just what a local-disk run implies."
