#!/bin/bash

############################################################################
# Run the EGPT unit test PR gate (matches .github/workflows/ci.yml)
# Usage: ./libs/agno/scripts/test.sh
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGNO_DIR="$(dirname ${CURR_DIR})"
source ${CURR_DIR}/_utils.sh

print_heading "Running EGPT unit test PR gate"

print_heading "Running: pytest ${AGNO_DIR}/tests/unit with coverage"
pytest ${AGNO_DIR}/tests/unit \
  --cov=${AGNO_DIR}/egpt \
  --cov-report=term-missing \
  --cov-report=json:coverage-egpt.json \
  --cov-report=html
