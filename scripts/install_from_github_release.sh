#!/usr/bin/env bash
############################################################################
# Download first-party EGPT wheels from a GitHub Release and pip-install them.
#
# Installs the full first-party closure (egpt, egptctl, egpt-infra) from
# authenticated release assets with --no-deps, then installs remaining
# third-party requirements from PyPI. Never resolves egpt / egptctl /
# egpt-infra by name from public indexes (public egpt==0.0.1 is unrelated).
#
# Usage:
#   ./scripts/install_from_github_release.sh [primary_dist] [version]
#
# primary_dist is informational (default: egpt). All three first-party wheels
# are always downloaded and installed.
#
# Env:
#   OWNER, REPO, GITHUB_TOKEN/GH_TOKEN
############################################################################

set -euo pipefail

PRIMARY_DIST="${1:-egpt}"
OWNER="${OWNER:-Saturam-Inc}"
REPO="${REPO:-saturam-agent-framework}"
FULL_REPO="${OWNER}/${REPO}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMING=(python3 "${ROOT_DIR}/scripts/release_naming.py")

if [[ -z "${GITHUB_TOKEN:-}" && -z "${GH_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN or GH_TOKEN is required" >&2
  exit 1
fi
export GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN}}"

if [[ -n "${2:-}" ]]; then
  VERSION="$2"
else
  case "${PRIMARY_DIST}" in
    egpt) PKG_DIR="${ROOT_DIR}/libs/agno" ;;
    egptctl) PKG_DIR="${ROOT_DIR}/libs/egptctl" ;;
    egpt-infra) PKG_DIR="${ROOT_DIR}/libs/agno_infra" ;;
    *)
      echo "Unknown dist_name ${PRIMARY_DIST}; pass version as \$2" >&2
      exit 1
      ;;
  esac
  VERSION="$("${NAMING[@]}" version "${PKG_DIR}/pyproject.toml")"
fi

TAG="$("${NAMING[@]}" tag "${VERSION}")"
WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

FIRST_PARTY=(egpt egptctl egpt-infra)

download_wheel() {
  local dist_name="$1"
  local wheel_name
  wheel_name="$("${NAMING[@]}" wheel-name "${dist_name}")"
  local attempt=1
  local max_attempts=8
  until gh release download "${TAG}" \
    --repo "${FULL_REPO}" \
    --pattern "${wheel_name}-${VERSION}-*.whl" \
    --dir "${WORKDIR}" \
    --clobber; do
    if [[ "${attempt}" -ge "${max_attempts}" ]]; then
      echo "Failed to download ${wheel_name}-${VERSION} wheel after ${max_attempts} attempts" >&2
      return 1
    fi
    local sleep_for=$((attempt * 3))
    echo "Asset not ready yet for ${wheel_name} (attempt ${attempt}/${max_attempts}); sleeping ${sleep_for}s"
    sleep "${sleep_for}"
    attempt=$((attempt + 1))
  done
}

echo "Downloading first-party wheels for ${TAG} from ${FULL_REPO}"
for dist in "${FIRST_PARTY[@]}"; do
  download_wheel "${dist}"
done

shopt -s nullglob
wheels=()
for dist in "${FIRST_PARTY[@]}"; do
  wheel_name="$("${NAMING[@]}" wheel-name "${dist}")"
  matches=("${WORKDIR}"/${wheel_name}-${VERSION}-*.whl)
  if [[ ${#matches[@]} -eq 0 ]]; then
    echo "No wheel downloaded for ${dist} (${wheel_name}-${VERSION})" >&2
    exit 1
  fi
  wheels+=("${matches[0]}")
done

export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"
unset PIP_EXTRA_INDEX_URL || true

python3 -m pip install --upgrade pip
echo "Installing first-party wheels with --no-deps: ${wheels[*]}"
python3 -m pip install --no-cache-dir --no-deps "${wheels[@]}"

mapfile -t THIRD_PARTY < <("${NAMING[@]}" third-party-requires "${wheels[@]}")
# Also pull egpt[infra]-style third-party extras used by smoke (docker optional).
# Base METADATA already covers core deps; infra extras like docker/boto3 are
# optional — install egpt-infra's unconditional deps only (already covered).

if [[ ${#THIRD_PARTY[@]} -gt 0 ]]; then
  echo "Installing third-party dependencies from PyPI (${#THIRD_PARTY[@]} reqs)"
  python3 -m pip install --no-cache-dir "${THIRD_PARTY[@]}"
else
  echo "No third-party requirements found in wheel METADATA"
fi

echo "Installed first-party closure for primary=${PRIMARY_DIST} version=${VERSION}"
python3 -c "import egpt, egptctl; import egpt.infra; print('closure OK', egpt.__version__)"
