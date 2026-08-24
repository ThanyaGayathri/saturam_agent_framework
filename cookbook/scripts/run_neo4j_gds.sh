#!/bin/bash
# Neo4j + the GDS plugin, for the PPR leg's integration suite (SM-H4 #134).
#
# A separate container and port from `run_neo4j.sh`'s SM-G1 instance: GDS is an optional plugin
# most of this substrate's tests never need, and SM-R2 section 5.3 flags the plugin's bundled
# license text as contradictory and not yet legal-reviewed for production -- keeping it out of the
# default dev instance means nobody pulls it in by running the wrong script.
#
# Community edition + the free GDS tier: SM-R2's live probe
# (benchmarks/memory_graph_engine/run_ppr_route.py) verified personalized `gds.pageRank` runs on
# this combination (`gdsEdition: Unlicensed`), which is the capability SM-H4 needs. That probe does
# not settle the license question -- see SM-R2's SPIKE.md section 5.3 before using this outside a
# local dev/CI run.

docker run -d \
  -e NEO4J_AUTH=neo4j/smh4testpassword \
  -e NEO4J_PLUGINS='["graph-data-science"]' \
  -p 7692:7687 \
  -p 7477:7474 \
  --name neo4j-memory-gds \
  neo4j:5

echo "Bolt: bolt://localhost:7692   Browser: http://localhost:7477"
echo "Run the PPR integration suite with:"
echo "  EGPT_INTEGRATION_NEO4J_GDS=1 pytest libs/agno/tests/integration/memory/neo4j/test_graph_ppr.py"
