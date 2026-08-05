#!/bin/bash

############################################################################
# Generate requirements.txt from requirements.in
############################################################################

echo "Generating requirements.txt"

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# requirements.in installs egpt as an editable path relative to the repo root, so
# compile from there -- uv resolves those paths against the current directory.
cd "$(git -C "${CURR_DIR}" rev-parse --show-toplevel)" || exit 1

UV_CUSTOM_COMPILE_COMMAND="./generate_requirements.sh" \
  uv pip compile ${CURR_DIR}/requirements.in --no-cache --upgrade -o ${CURR_DIR}/requirements.txt
