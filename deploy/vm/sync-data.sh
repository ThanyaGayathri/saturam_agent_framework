#!/usr/bin/env bash
# Copy the local demo state -- the control-plane and warehouse Postgres databases and the
# Neo4j ontology/semantic graph -- from this laptop's containers to the VM's compose stack.
# Run from the laptop, from the repository root, after deploy.sh has the stack up.
#
#     deploy/vm/sync-data.sh
#
# Replaces the VM's data wholesale (drops and recreates both databases, replaces Neo4j's
# /data). The VM's api and consolidator are stopped for the restore and started again after.
# The local Neo4j container is stopped for the few seconds its data directory is archived,
# so the copy is consistent, then started again.
#
# Overrides: VM_HOST, VM_KEY, LOCAL_PG (container), LOCAL_NEO4J (container).
# RESTORE_ONLY=1 skips the dump and upload and restores what ~/egpt/sync on the VM already holds.
set -euo pipefail

VM_HOST="${VM_HOST:-azureuser@20.64.179.94}"
VM_KEY="${VM_KEY:-$HOME/AICoE/pem/egpt-azure.pem}"
LOCAL_PG="${LOCAL_PG:-smv1-pgvector-5532}"
LOCAL_NEO4J="${LOCAL_NEO4J:-egpt-retail-neo4j}"
REMOTE_REPO="${REMOTE_REPO:-saturam-agent-framework}"
SSH=(ssh -i "$VM_KEY" "$VM_HOST")
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -z "${RESTORE_ONLY:-}" ]; then
echo "== dump local Postgres ($LOCAL_PG)"
for db in ai egpt_warehouse; do
    docker exec "$LOCAL_PG" pg_dump -U ai -Fc "$db" > "$WORK/$db.dump"
    ls -la "$WORK/$db.dump" | awk '{print "   " $NF, $5, "bytes"}'
done

echo "== archive local Neo4j data ($LOCAL_NEO4J, stopped briefly)"
docker stop "$LOCAL_NEO4J" >/dev/null
docker run --rm --volumes-from "$LOCAL_NEO4J" -v "$WORK:/out" alpine \
    sh -c 'cd /data && tar czf /out/neo4j-data.tgz .'
docker start "$LOCAL_NEO4J" >/dev/null
ls -la "$WORK/neo4j-data.tgz" | awk '{print "   " $NF, $5, "bytes"}'

echo "== upload"
"${SSH[@]}" 'mkdir -p ~/egpt/sync'
scp -q -i "$VM_KEY" "$WORK/ai.dump" "$WORK/egpt_warehouse.dump" "$WORK/neo4j-data.tgz" "$VM_HOST:~/egpt/sync/"
fi

echo "== restore on the VM"
"${SSH[@]}" "bash ~/$REMOTE_REPO/deploy/vm/restore-data.sh"
echo "== done"
