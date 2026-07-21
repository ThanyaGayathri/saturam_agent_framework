#!/bin/bash

############################################################################
# Validate the agno_aws library using ruff and mypy
# Usage: ./libs/infra/agno_aws/scripts/validate.sh
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGNO_INFRA_DIR="$(dirname ${CURR_DIR})"
source ${CURR_DIR}/_utils.sh

print_heading "Validating agno_infra"

# Aggregate exit codes so a failure in either tool fails the script,
# while still running both tools to surface all failures in one pass.
EXIT_CODE=0

print_heading "Running: ruff check ${AGNO_INFRA_DIR}"
ruff check ${AGNO_INFRA_DIR} || EXIT_CODE=1

# Typecheck the egpt package by module name. Path-based mypy on agno_infra
# mis-resolves namespace packages (egpt/docker -> top-level "docker") after the
# shared-namespace layout (no egpt/__init__.py).
print_heading "Running: mypy -p egpt --config-file ${AGNO_INFRA_DIR}/pyproject.toml"
(
  cd "${AGNO_INFRA_DIR}"
  mypy -p egpt --config-file "${AGNO_INFRA_DIR}/pyproject.toml"
) || EXIT_CODE=1

exit ${EXIT_CODE}
