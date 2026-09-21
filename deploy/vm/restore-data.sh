#!/usr/bin/env bash
# The VM half of sync-data.sh: restore the dumps in ~/egpt/sync into the compose stack.
# Run on the VM; sync-data.sh calls it over ssh after uploading. Usable on its own to
# re-restore the last uploaded snapshot.
#
#     bash ~/saturam-agent-framework/deploy/vm/restore-data.sh
#
# Stops api + consolidator, drops and recreates both databases from the dumps, replaces
# Neo4j's /data from the archive, re-runs the idempotent migrations (roles are cluster-wide
# and outside a dump; the restore ran --no-privileges), starts everything, checks /health.
set -euo pipefail

REPO="${REPO:-$HOME/saturam-agent-framework}"; ENV_FILE="${ENV_FILE:-$HOME/egpt/egpt.env}"
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
"${C[@]}" run --rm -T --no-deps warehouse-migrate </dev/null >/dev/null
"${C[@]}" run --rm -T --no-deps migrate </dev/null >/dev/null

"${C[@]}" stop neo4j >/dev/null
NEO=$("${C[@]}" ps -aq neo4j)
docker run --rm --volumes-from "$NEO" -v "$HOME/egpt/sync:/in:ro" alpine \
    sh -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null; cd /data && tar xzf /in/neo4j-data.tgz && chown -R 7474:7474 /data'
"${C[@]}" up -d neo4j api consolidator >/dev/null
for _ in $(seq 1 40); do curl -fsS http://127.0.0.1:7777/health >/dev/null 2>&1 && break; sleep 3; done
curl -fsS http://127.0.0.1:7777/health || { echo "api not healthy after restore:"; "${C[@]}" logs --tail 60 api; exit 1; }
echo
echo "   neo4j nodes: $(docker exec "$("${C[@]}" ps -q neo4j)" cypher-shell -u neo4j -p "$(grep "^EGPT_RETAIL_NEO4J_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)" --format plain "MATCH (n) RETURN count(n)" 2>/dev/null | tail -1)"
rm -f "$HOME/egpt/sync/"*.dump "$HOME/egpt/sync/"*.tgz
