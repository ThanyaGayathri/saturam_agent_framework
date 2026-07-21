#!/bin/bash

############################################################################
# Release egptctl to a GitHub Release (private asset channel)
# Usage: ./libs/egptctl/scripts/release_manual.sh
# Note:
#   build & gh must be available; set GITHUB_TOKEN (contents:write)
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EGPTCTL_DIR="$(dirname ${CURR_DIR})"
REPO_ROOT="$(cd "${CURR_DIR}/../../.." && pwd)"
source ${CURR_DIR}/_utils.sh

main() {
  print_heading "Releasing *egptctl* to GitHub Release assets"

  cd ${EGPTCTL_DIR}
  print_heading "pwd: $(pwd)"

  VERSION=$(python3 "${REPO_ROOT}/scripts/release_naming.py" version pyproject.toml)
  print_heading "Version: ${VERSION} (tag v${VERSION})"

  if [[ -z "${GITHUB_TOKEN:-}" && -z "${GH_TOKEN:-}" ]]; then
    print_heading "GITHUB_TOKEN or GH_TOKEN is required"
    exit 1
  fi

  print_heading "Proceed?"
  space_to_continue

  print_heading "Build and upload egptctl to GitHub Release"
  export OWNER="${OWNER:-Saturam-Inc}"
  export REPO="${REPO:-saturam-agent-framework}"
  export GITHUB_EVENT_NAME="${GITHUB_EVENT_NAME:-workflow_dispatch}"
  "${REPO_ROOT}/scripts/publish_github_release.sh" libs/egptctl egptctl

  print_heading "Done. Install with:"
  print_info "./scripts/install_from_github_release.sh egptctl"
}

main "$@"
