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
set -euo pipefail

VM_HOST="${VM_HOST:-azureuser@20.64.179.94}"
VM_KEY="${VM_KEY:-$HOME/AICoE/pem/egpt-azure.pem}"
LOCAL_PG="${LOCAL_PG:-smv1-pgvector-5532}"
LOCAL_NEO4J="${LOCAL_NEO4J:-egpt-retail-neo4j}"
REMOTE_REPO="${REMOTE_REPO:-saturam-agent-framework}"
REMOTE_ENV="${REMOTE_ENV:-egpt/egpt.env}"
SSH=(ssh -i "$VM_KEY" "$VM_HOST")
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

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

echo "== restore on the VM"
"${SSH[@]}" bash -s "$REMOTE_REPO" "$REMOTE_ENV" <<'REMOTE'
set -euo pipefail
REPO="$HOME/$1"; ENV_FILE="$HOME/$2"
export EGPT_VM_ENV_FILE="$ENV_FILE"
C=(docker compose -f "$REPO/deploy/vm/compose.vm.yaml" --env-file "$ENV_FILE")
"${C[@]}" stop api consolidator >/dev/null
PG=$("${C[@]}" ps -q postgres)
for db in ai egpt_warehouse; do
    docker exec "$PG" psql -U ai -d postgres -qc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$db' AND pid<>pg_backend_pid()" >/dev/null
    docker exec "$PG" psql -U ai -d postgres -qc "DROP DATABASE IF EXISTS $db"
    docker exec "$PG" psql -U ai -d postgres -qc "CREATE DATABASE $db OWNER ai"
    docker exec -i "$PG" pg_restore -U ai -d "$db" --no-owner --no-privileges < "$HOME/egpt/sync/$db.dump" \
        || echo "   pg_restore reported errors for $db (usually harmless: extensions/roles already present)"
    echo "   $db: $(docker exec "$PG" psql -U ai -d "$db" -tAc "select count(*) from pg_tables where schemaname not in ('pg_catalog','information_schema')") tables"
done
# Roles are cluster-wide and not in a database dump, and the restore ran --no-privileges: the
# migrations are idempotent, so re-running them restores egpt_reader's grants (065, 075) and
# stamps anything the dump predates.
"${C[@]}" run --rm --no-deps warehouse-migrate >/dev/null
"${C[@]}" run --rm --no-deps migrate >/dev/null

"${C[@]}" stop neo4j >/dev/null
NEO=$("${C[@]}" ps -aq neo4j)
docker run --rm --volumes-from "$NEO" -v "$HOME/egpt/sync:/in:ro" alpine \
    sh -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null; cd /data && tar xzf /in/neo4j-data.tgz && chown -R 7474:7474 /data'
"${C[@]}" up -d neo4j api consolidator >/dev/null
for _ in $(seq 1 40); do curl -fsS http://127.0.0.1:7777/health >/dev/null 2>&1 && break; sleep 3; done
curl -fsS http://127.0.0.1:7777/health && echo
rm -f "$HOME/egpt/sync/"*.dump "$HOME/egpt/sync/"*.tgz
REMOTE
echo "== done"
