#!/usr/bin/env bash
#
# Run the retail API locally for the interface on port 3001 (K16).
#
# Sources the gitignored credential files at the repo root when they exist -- gmail.env,
# salesforce.env, azure_key.env -- so the document-to-record executors can resolve
# `env:gmail` / `env:salesforce` and the extraction step has a model. Uses the settings'
# defaults for Postgres (localhost:5532) and Neo4j (localhost:7701), the ports
# deploy/compose.yaml publishes. Every variable can be overridden from the environment.
#
#   ./scripts/run_local_api.sh              # port 7777
#   EGPT_RETAIL_USE_CASE_POLLER=true ./scripts/run_local_api.sh   # with the in-process poller
#
set -euo pipefail
cd "$(dirname "$0")/.."
for f in gmail.env salesforce.env azure_key.env; do
  if [ -f "$f" ]; then
    set -a; # shellcheck disable=SC1090
    source "$f"; set +a
  fi
done
export EGPT_CORS_ORIGIN_LIST="${EGPT_CORS_ORIGIN_LIST:-[\"http://localhost:3001\",\"http://localhost:3000\"]}"
export EGPT_RETAIL_CUSTOMER_SLUG="${EGPT_RETAIL_CUSTOMER_SLUG:-northwind}"
exec .venv/bin/python -m egpt_retail.entrypoints api --port "${EGPT_RETAIL_API_PORT:-7777}"
