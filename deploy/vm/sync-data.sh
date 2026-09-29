#!/usr/bin/env bash
# Copy the demo state -- the control-plane and warehouse Postgres databases and the Neo4j
# ontology/semantic graph -- to a VM's compose stack. The source is this laptop's containers
# by default, or another VM's stack with SOURCE_VM_HOST (one demo VM cloned onto another).
# Run from the laptop, from the repository root, after deploy.sh has the target stack up.
#
#     deploy/vm/sync-data.sh
#     SOURCE_VM_HOST=azureuser@20.64.179.94 SOURCE_VM_KEY=~/AICoE/pem/egpt-azure.pem \
#         VM_HOST=azureuser@20.169.82.164 VM_KEY=~/AICoE/pem/mcfadyen_egpt.pem \
#         deploy/vm/sync-data.sh
#
# Replaces the VM's data wholesale (drops and recreates both databases, replaces Neo4j's
# /data). The VM's api and consolidator are stopped for the restore and started again after.
# The local Neo4j container is stopped for the few seconds its data directory is archived,
# so the copy is consistent, then started again.
#
# Overrides: VM_HOST, VM_KEY (the target), LOCAL_PG / LOCAL_NEO4J (the source containers when
# the source is this laptop), SOURCE_VM_HOST / SOURCE_VM_KEY (dump another VM's stack instead;
# its containers are the compose stack's, SOURCE_PG / SOURCE_NEO4J override them). Either way
# the source's Neo4j is stopped for the few seconds its data directory is archived, so with
# SOURCE_VM_HOST that VM's own site is briefly without its graph.
# RESTORE_ONLY=1 skips the dump and upload and restores what ~/egpt/sync on the VM already holds.
set -euo pipefail

VM_HOST="${VM_HOST:-azureuser@20.64.179.94}"
VM_KEY="${VM_KEY:-$HOME/AICoE/pem/egpt-azure.pem}"
LOCAL_PG="${LOCAL_PG:-smv1-pgvector-5532}"
LOCAL_NEO4J="${LOCAL_NEO4J:-egpt-retail-neo4j}"
SOURCE_PG="${SOURCE_PG:-egpt-vm-postgres-1}"
SOURCE_NEO4J="${SOURCE_NEO4J:-egpt-vm-neo4j-1}"
REMOTE_REPO="${REMOTE_REPO:-saturam-agent-framework}"
SSH=(ssh -i "$VM_KEY" "$VM_HOST")
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "${SOURCE_VM_HOST:-}" ] && [ -z "${RESTORE_ONLY:-}" ]; then
SRC_SSH=(ssh -i "${SOURCE_VM_KEY:-$VM_KEY}" "$SOURCE_VM_HOST")

echo "== dump Postgres on $SOURCE_VM_HOST ($SOURCE_PG)"
for db in ai egpt_warehouse; do
    "${SRC_SSH[@]}" "docker exec $SOURCE_PG pg_dump -U ai -Fc $db" > "$WORK/$db.dump"
    [ -s "$WORK/$db.dump" ] || { echo "   $db dump is empty -- is $SOURCE_PG running there?"; exit 1; }
    ls -la "$WORK/$db.dump" | awk '{print "   " $NF, $5, "bytes"}'
done

# The source VM's Neo4j is stopped for the archive and started again by the remote trap, so an
# ssh that dies mid-copy does not leave that VM's graph down.
echo "== archive Neo4j on $SOURCE_VM_HOST ($SOURCE_NEO4J, stopped briefly)"
"${SRC_SSH[@]}" "set -e
    trap 'docker start $SOURCE_NEO4J >/dev/null 2>&1' EXIT
    docker stop $SOURCE_NEO4J >/dev/null
    docker run --rm --volumes-from $SOURCE_NEO4J alpine sh -c 'cd /data && tar czf - .'" \
    > "$WORK/neo4j-data.tgz"
[ -s "$WORK/neo4j-data.tgz" ] || { echo "   the Neo4j archive is empty"; exit 1; }
tar tzf "$WORK/neo4j-data.tgz" >/dev/null || { echo "   the Neo4j archive did not unpack"; exit 1; }
ls -la "$WORK/neo4j-data.tgz" | awk '{print "   " $NF, $5, "bytes"}'

echo "== upload"
"${SSH[@]}" 'mkdir -p ~/egpt/sync'
scp -q -i "$VM_KEY" "$WORK/ai.dump" "$WORK/egpt_warehouse.dump" "$WORK/neo4j-data.tgz" "$VM_HOST:~/egpt/sync/"

elif [ -z "${RESTORE_ONLY:-}" ]; then
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
