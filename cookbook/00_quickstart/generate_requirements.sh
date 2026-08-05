#!/bin/bash

############################################################################
# Generate requirements.txt from requirements.in
############################################################################

echo "Generating requirements.txt"

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${CURR_DIR}" rev-parse --show-toplevel)"
REL_DIR="${CURR_DIR#"${REPO_ROOT}/"}"
REQ_TXT="${CURR_DIR}/requirements.txt"

# requirements.in installs egpt as an editable path relative to the repo root, so
# compile from there -- uv resolves those paths against the current directory.
cd "${REPO_ROOT}" || exit 1

# The recorded command has to be runnable from where we now are, i.e. the repo root.
UV_CUSTOM_COMPILE_COMMAND="./${REL_DIR}/generate_requirements.sh" \
  uv pip compile "${CURR_DIR}/requirements.in" --no-cache --upgrade -o "${REQ_TXT}"

# uv rewrites the whole output file and does not carry comments over from
# requirements.in, so the note about the editable path has to be re-inserted after
# every regeneration -- otherwise the next run silently drops it.
NOTE="# egpt comes from this repo's sources, not PyPI (upstream \`agno\` pin removed,
# EGPT-0.13). Run this from the repo root -- uv resolves the editable path below
# against your current directory:
#     uv pip install -r ${REL_DIR}/requirements.txt"

{ head -2 "${REQ_TXT}"; printf '%s\n' "${NOTE}"; tail -n +3 "${REQ_TXT}"; } > "${REQ_TXT}.tmp" \
  && mv "${REQ_TXT}.tmp" "${REQ_TXT}"
