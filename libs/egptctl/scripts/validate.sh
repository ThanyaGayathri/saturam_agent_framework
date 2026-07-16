#!/bin/bash

############################################################################
# Validate the egptctl library using ruff and mypy
# Usage: ./libs/egptctl/scripts/validate.sh
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EGPTCTL_DIR="$(dirname "${CURR_DIR}")"
source "${CURR_DIR}/_utils.sh"

print_heading "Validating egptctl"

EXIT_CODE=0

print_heading "Running: ruff check ${EGPTCTL_DIR}"
ruff check "${EGPTCTL_DIR}" || EXIT_CODE=1

print_heading "Running: mypy ${EGPTCTL_DIR}/egptctl --config-file ${EGPTCTL_DIR}/pyproject.toml"
mypy "${EGPTCTL_DIR}/egptctl" --config-file "${EGPTCTL_DIR}/pyproject.toml" || EXIT_CODE=1

exit "${EXIT_CODE}"
