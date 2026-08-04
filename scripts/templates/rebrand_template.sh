#!/usr/bin/env bash
############################################################################
#
#    EGPT Starter Template Rebrand
#
#    Builds a rebranded copy of an upstream agno-agi/agentos-<flavor>
#    starter template, ready to review and publish as
#    Saturam-Inc/egpt-os-<flavor>. Publishing is a manual, reviewed step —
#    see scripts/templates/README.md for the full procedure.
#
#    Usage:
#      ./scripts/templates/rebrand_template.sh <flavor> [target-dir]
#      ./scripts/templates/rebrand_template.sh check <dir>
#
#    Flavors: docker, aws, fly, gcp, railway
#
#    Env:
#      EGPT_VERSION   framework release the template pins (default 0.1.0)
#      UPSTREAM_ORG   upstream GitHub org to clone from (default agno-agi)
#
############################################################################

set -euo pipefail

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EGPT_VERSION="${EGPT_VERSION:-0.1.0}"
UPSTREAM_ORG="${UPSTREAM_ORG:-agno-agi}"
FLAVORS=(docker aws fly gcp railway)

# Grep for upstream-brand leakage. Allowed residuals:
#   - openinference-instrumentation-agno: real third-party PyPI package
#   - AGENTOS_URL / AGENTOS_MCP_SIGNING_KEY: env names the framework still reads
#   - agno_pat_: the service-account token prefix the framework still mints
#     (libs/agno/egpt/os/service_accounts.py TOKEN_PREFIX)
#   - NOTICE: Apache-2.0 attribution to the upstream template
#   - LICENSE: retains the upstream copyright notice, as Apache-2.0 requires
# The pattern requires "agno" not preceded by "i" so that ordinary words like
# "diagnostic" / "diagnose" (which contain "agno") do not trip the check.
check_dir() {
    local dir="$1"
    # [P2] Reject nonexistent/unreadable directories before grep. The trailing || true
    # in the grep pipeline suppresses both the expected "no matches" (exit 1) AND genuine
    # errors (exit 2). This guard ensures a typo'd path fails fast instead of silently
    # passing the pre-publication gate (rasiksuhailali's review feedback).
    if [[ ! -d "${dir}" || ! -r "${dir}" ]]; then
        echo "Not a readable directory: ${dir}" >&2
        return 2
    fi
    local hits
    hits="$(grep -rIn --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=.mypy_cache --exclude-dir=.ruff_cache --exclude=NOTICE --exclude=LICENSE -i -E '(^|[^i])agno|agentos' "${dir}" \
        | grep -v 'openinference-instrumentation-agno' \
        | grep -v 'AGENTOS_URL' \
        | grep -v 'AGENTOS_MCP_SIGNING_KEY' \
        | grep -v 'agno_pat_' || true)"
    if [[ -n "${hits}" ]]; then
        echo "Upstream-brand leakage found in ${dir}:"
        echo ""
        echo "${hits}"
        return 1
    fi
    echo "No upstream-brand leakage found in ${dir}"
}

if [[ "${1:-}" == "check" ]]; then
    check_dir "${2:?Usage: rebrand_template.sh check <dir>}"
    exit $?
fi

FLAVOR="${1:?Usage: rebrand_template.sh <flavor> [target-dir] | check <dir>}"
if [[ ! " ${FLAVORS[*]} " == *" ${FLAVOR} "* ]]; then
    echo "Unknown flavor: ${FLAVOR} (expected one of: ${FLAVORS[*]})" >&2
    exit 2
fi

TARGET="${2:-egpt-os-${FLAVOR}}"
if [[ -e "${TARGET}" ]]; then
    echo "Target ${TARGET} already exists; remove it or pass a different directory." >&2
    exit 2
fi

UPSTREAM_URL="https://github.com/${UPSTREAM_ORG}/agentos-${FLAVOR}"
echo "Cloning ${UPSTREAM_URL}"
git clone --depth 1 "${UPSTREAM_URL}" "${TARGET}"
rm -rf "${TARGET}/.git"

echo "Adding scripts/install_egpt.sh and NOTICE"
cp "${CURR_DIR}/files/install_egpt.sh" "${TARGET}/scripts/install_egpt.sh"
chmod +x "${TARGET}/scripts/install_egpt.sh"

cat > "${TARGET}/NOTICE" <<EOF
This project is derived from the AgentOS starter template published by
Agno (https://github.com/${UPSTREAM_ORG}/agentos-${FLAVOR}), used under the
Apache License 2.0. See LICENSE for the full license text.

Modifications: rebranded for the EGPT framework by Saturam.
EOF

echo "Running codemod"
CODEMOD_STATUS=0
python3 "${CURR_DIR}/rebrand_codemod.py" "${TARGET}" "${FLAVOR}" "${EGPT_VERSION}" || CODEMOD_STATUS=$?

echo ""
echo "Residual report (expected until the manual pass below is done):"
echo ""
check_dir "${TARGET}" || true

cat <<EOF

Manual pass still required in ${TARGET}:
  1. README.md — rewrite: strip os.agno.com UI sections, utm links and
     upstream marketing; keep localhost:8000/docs and the MCP flow as the
     primary interface. Fly/Railway flavors: document that GITHUB_TOKEN must
     be configured as a platform build secret.
  2. example.env — rewrite the comment blocks that reference os.agno.com
     (JWT key generation) and review remaining comments.
  3. AGENTS.md / CLAUDE.md / .agents/skills — review prose; drop stale
     upstream workflow notes (e.g. the egptctl pin-floor comment).
  4. Regenerate requirements.txt (needs GITHUB_TOKEN):
       cd ${TARGET} && ./scripts/generate_requirements.sh
  5. Validate: ./scripts/venv_setup.sh && ./scripts/validate.sh, and for
     flavors with a Dockerfile:
       docker build --secret id=github_token,env=GITHUB_TOKEN .
  6. Re-run the leak check until clean:
       ${CURR_DIR}/rebrand_template.sh check ${TARGET}
  7. After publishing, add the EGPT_RELEASES_TOKEN secret to the new repo so
     .github/workflows/validate.yml can download the egpt wheels.

Publish (after review): see scripts/templates/README.md
EOF

exit "${CODEMOD_STATUS}"
