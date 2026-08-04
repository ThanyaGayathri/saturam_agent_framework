#!/usr/bin/env bash
############################################################################
#
#    Install the EGPT framework wheels from a GitHub Release.
#
#    The egpt distribution is not published on PyPI (the public egpt==0.0.1
#    project is unrelated). This script downloads the first-party wheel(s)
#    from a release of the framework repository and installs them with
#    --no-deps; third-party dependencies come from requirements.txt.
#
#    Usage:
#      ./scripts/install_egpt.sh                       # install into the active environment
#      ./scripts/install_egpt.sh --system              # install into the system python (Docker)
#      ./scripts/install_egpt.sh --venv <dir>          # install into the given virtualenv
#      ./scripts/install_egpt.sh --download-only <dir> # download wheels, do not install
#
#    Env:
#      GITHUB_TOKEN   required — token with read access to the framework releases
#      EGPT_REPO      default Saturam-Inc/saturam-agent-framework
#      EGPT_VERSION   default 0.1.0 (release tag v${EGPT_VERSION})
#      EGPT_DISTS     default "egpt" — space-separated first-party dists to install
#
############################################################################

set -euo pipefail

EGPT_REPO="${EGPT_REPO:-Saturam-Inc/saturam-agent-framework}"
EGPT_VERSION="${EGPT_VERSION:-0.1.0}"
EGPT_DISTS="${EGPT_DISTS:-egpt}"
API_ROOT="https://api.github.com/repos/${EGPT_REPO}"
TAG="v${EGPT_VERSION}"

MODE="env"
TARGET_DIR=""
case "${1:-}" in
    "") ;;
    --system) MODE="system" ;;
    --venv) MODE="venv"; TARGET_DIR="${2:?--venv requires a directory}" ;;
    --download-only) MODE="download"; TARGET_DIR="${2:?--download-only requires a directory}" ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
esac

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "GITHUB_TOKEN is required to download the egpt wheels from ${EGPT_REPO}" >&2
    exit 1
fi

if [[ "${MODE}" == "download" ]]; then
    WHEEL_DIR="${TARGET_DIR}"
    mkdir -p "${WHEEL_DIR}"
else
    WHEEL_DIR="$(mktemp -d)"
    trap 'rm -rf "${WHEEL_DIR}"' EXIT
fi

echo "Fetching release ${TAG} from ${EGPT_REPO}"
RELEASE_FILE="$(mktemp)"
curl -fsSL --retry 5 \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -o "${RELEASE_FILE}" \
    "${API_ROOT}/releases/tags/${TAG}"

# Resolve "asset_id asset_name" per requested dist. Wheel filenames use
# PEP 427 normalization: hyphens in the dist name become underscores.
# shellcheck disable=SC2086
ASSETS="$(python3 - "${RELEASE_FILE}" "${EGPT_VERSION}" ${EGPT_DISTS} <<'PY'
import json
import re
import sys

release_file, version = sys.argv[1], sys.argv[2]
dists = sys.argv[3:]
with open(release_file) as fh:
    assets = json.load(fh).get("assets", [])
lines = []
for dist in dists:
    wheel = dist.replace("-", "_")
    pattern = re.compile("^" + re.escape(wheel) + "-" + re.escape(version) + "-.+\\.whl$")
    match = next((a for a in assets if pattern.match(a["name"])), None)
    if match is None:
        sys.stderr.write("No wheel for " + dist + "==" + version + " among release assets\n")
        sys.exit(1)
    lines.append(str(match["id"]) + " " + match["name"])
print("\n".join(lines))
PY
)"
rm -f "${RELEASE_FILE}"

while read -r asset_id asset_name; do
    echo "Downloading ${asset_name}"
    curl -fsSL --retry 5 \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "Accept: application/octet-stream" \
        -o "${WHEEL_DIR}/${asset_name}" \
        "${API_ROOT}/releases/assets/${asset_id}"
done <<< "${ASSETS}"

if [[ "${MODE}" == "download" ]]; then
    echo "Wheels downloaded to ${WHEEL_DIR}"
    exit 0
fi

shopt -s nullglob
WHEELS=("${WHEEL_DIR}"/*.whl)
if [[ ${#WHEELS[@]} -eq 0 ]]; then
    echo "No wheels downloaded" >&2
    exit 1
fi

case "${MODE}" in
    system)
        uv pip install --system --no-cache --no-deps "${WHEELS[@]}"
        ;;
    venv)
        VIRTUAL_ENV="${TARGET_DIR}" uv pip install --no-cache --no-deps "${WHEELS[@]}"
        ;;
    env)
        if command -v uv &> /dev/null; then
            uv pip install --no-cache --no-deps "${WHEELS[@]}"
        else
            python3 -m pip install --no-cache-dir --no-deps "${WHEELS[@]}"
        fi
        ;;
esac

echo "Installed ${EGPT_DISTS} ${EGPT_VERSION} from ${EGPT_REPO} ${TAG}"
