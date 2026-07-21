#!/usr/bin/env bash
############################################################################
# Build and publish a distribution to a GitHub Release (private asset channel).
#
# GitHub Packages has no working PyPI registry (pypi.pkg.github.com returns 404),
# so we publish wheels/sdists as release assets on tag v<version>.
#
# Usage:
#   ./scripts/publish_github_release.sh <package_dir> <dist_name>
#
# Requires:
#   - gh CLI authenticated (GITHUB_TOKEN or GH_TOKEN)
#   - python with build installed
#   - OWNER / REPO env vars optional (default: Saturam-Inc/saturam-agent-framework)
#
# Env:
#   RELEASE_TAG     — optional tag from release:published (v0.1.0 or 0.1.0)
#   GITHUB_EVENT_NAME — workflow_dispatch creates a --draft release
#   ALLOW_DIRTY_RELEASE=1 — allow publishing from a dirty worktree
#   SOURCE_DATE_EPOCH — set automatically from HEAD if unset
#
# Skips upload when BOTH wheel and sdist already exist; uploads only missing
# artifacts if a prior run was partial. Never --clobber.
############################################################################

set -euo pipefail

PACKAGE_DIR="${1:?Usage: $0 <package_dir> <dist_name>}"
DIST_NAME="${2:?Usage: $0 <package_dir> <dist_name>}"
OWNER="${OWNER:-Saturam-Inc}"
REPO="${REPO:-saturam-agent-framework}"
FULL_REPO="${OWNER}/${REPO}"

if [[ -z "${GITHUB_TOKEN:-}" && -z "${GH_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN or GH_TOKEN is required" >&2
  exit 1
fi
export GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN}}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMING=(python3 "${ROOT_DIR}/scripts/release_naming.py")
ABS_PACKAGE_DIR="$(cd "${ROOT_DIR}/${PACKAGE_DIR}" && pwd)"

if [[ "${ALLOW_DIRTY_RELEASE:-}" != "1" ]]; then
  if ! git -C "${ROOT_DIR}" diff --quiet || ! git -C "${ROOT_DIR}" diff --cached --quiet; then
    echo "Refusing to publish from a dirty worktree (set ALLOW_DIRTY_RELEASE=1 to override)" >&2
    exit 1
  fi
fi

VERSION="$("${NAMING[@]}" version "${ABS_PACKAGE_DIR}/pyproject.toml")"
WHEEL_NAME="$("${NAMING[@]}" wheel-name "${DIST_NAME}")"
EXPECTED_TAG="$("${NAMING[@]}" tag "${VERSION}")"
EXPECTED_VER="$("${NAMING[@]}" normalize-tag "${EXPECTED_TAG}")"

if [[ -n "${RELEASE_TAG:-}" ]]; then
  RELEASE_VER="$("${NAMING[@]}" normalize-tag "${RELEASE_TAG}")"
  if [[ "${RELEASE_VER}" != "${EXPECTED_VER}" ]]; then
    echo "Release tag ${RELEASE_TAG} (version ${RELEASE_VER}) does not match ${DIST_NAME} version ${EXPECTED_VER}" >&2
    exit 1
  fi
  TAG="$("${NAMING[@]}" tag "${RELEASE_VER}")"
else
  TAG="${EXPECTED_TAG}"
fi

echo "Publishing ${DIST_NAME}==${VERSION} (artifacts: ${WHEEL_NAME}-${VERSION}*) to ${TAG} (${FULL_REPO})"

ensure_release() {
  if gh release view "${TAG}" --repo "${FULL_REPO}" >/dev/null 2>&1; then
    echo "Release ${TAG} already exists"
    return 0
  fi
  local draft_args=()
  # workflow_dispatch creates a draft so release:published does not re-trigger.
  if [[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]]; then
    draft_args+=(--draft)
    echo "Creating draft release ${TAG}"
  else
    echo "Creating release ${TAG}"
  fi
  gh release create "${TAG}" \
    --repo "${FULL_REPO}" \
    --title "${TAG}" \
    --notes "EGPT private distribution ${TAG} (wheels/sdists as release assets)." \
    --target "${GITHUB_SHA:-$(git -C "${ROOT_DIR}" rev-parse HEAD)}" \
    "${draft_args[@]}"
}

list_assets() {
  gh release view "${TAG}" --repo "${FULL_REPO}" --json assets --jq '.assets[].name' 2>/dev/null || true
}

assets_status() {
  list_assets | "${NAMING[@]}" assets-status "${DIST_NAME}" "${VERSION}"
}

ensure_release

STATUS="$(assets_status)"
if [[ "${STATUS}" == "complete" ]]; then
  echo "Wheel and sdist for ${DIST_NAME}==${VERSION} already on ${TAG}; skipping"
  exit 0
fi

export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "${ROOT_DIR}" log -1 --format=%ct)}"
echo "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}"

echo "Building ${DIST_NAME} ${VERSION} from ${ABS_PACKAGE_DIR}"
rm -rf "${ABS_PACKAGE_DIR}/dist"
python3 -m build "${ABS_PACKAGE_DIR}"

shopt -s nullglob
to_upload=()
case "${STATUS}" in
  missing)
    to_upload+=("${ABS_PACKAGE_DIR}/dist"/${WHEEL_NAME}-${VERSION}*)
    ;;
  wheel_only)
    to_upload+=("${ABS_PACKAGE_DIR}/dist"/${WHEEL_NAME}-${VERSION}.tar.gz)
    ;;
  sdist_only)
    to_upload+=("${ABS_PACKAGE_DIR}/dist"/${WHEEL_NAME}-${VERSION}-*.whl)
    ;;
esac

# Filter to existing files only.
existing=()
for f in "${to_upload[@]+"${to_upload[@]}"}"; do
  [[ -f "${f}" ]] && existing+=("${f}")
done

if [[ ${#existing[@]} -eq 0 ]]; then
  echo "No build artifacts to upload for ${WHEEL_NAME}-${VERSION}* (status=${STATUS})" >&2
  ls -la "${ABS_PACKAGE_DIR}/dist" >&2 || true
  exit 1
fi

echo "Uploading ${#existing[@]} artifact(s) to ${TAG} (no clobber)"
gh release upload "${TAG}" --repo "${FULL_REPO}" "${existing[@]}"
echo "Uploaded: ${existing[*]}"

if command -v gh >/dev/null 2>&1 && gh attestation write --help >/dev/null 2>&1; then
  echo "Writing attestations for uploaded artifacts"
  gh attestation write "${existing[@]}" --repo "${FULL_REPO}" || echo "Warning: attestation write failed (continuing)"
fi
