#!/bin/bash

############################################################################
# Format the egptctl library using ruff
# Usage: ./libs/egptctl/scripts/format.sh
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EGPTCTL_DIR="$(dirname ${CURR_DIR})"
source ${CURR_DIR}/_utils.sh

print_heading "Formatting egptctl"

print_heading "Running: ruff format ${EGPTCTL_DIR}"
ruff format ${EGPTCTL_DIR}

print_heading "Running: ruff check --select I --fix ${EGPTCTL_DIR}"
ruff check --select I --fix ${EGPTCTL_DIR}
