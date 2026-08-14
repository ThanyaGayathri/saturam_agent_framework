#!/bin/bash
# Neo4j for the memory graph substrate (SM-G1).
#
# Community edition, which is what SM-R2 ratified for dev, CI, single-tenant and pilot use.
# Production multi-tenant needs Enterprise: Community denies CREATE DATABASE, so tenants share one
# graph and isolation is the substrate's property filter rather than the engine's.
#
# Ports are offset from the defaults so this does not collide with a Neo4j already running locally.

docker run -d \
  -e NEO4J_AUTH=neo4j/smg1testpassword \
  -p 7691:7687 \
  -p 7476:7474 \
  -v neo4j_memory_data:/data \
  --name neo4j-memory \
  neo4j:5

echo "Bolt: bolt://localhost:7691   Browser: http://localhost:7476"
echo "Run the substrate's integration suite with:"
echo "  EGPT_INTEGRATION_NEO4J=1 pytest libs/agno/tests/integration/memory"
