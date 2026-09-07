#!/bin/bash

############################################################################
#
#    EGPT Validate
#
#    ruff check + mypy for egpt, egptctl and product, ruff + pattern + import check
#    for cookbook, the API contract check, brand audit for the repo. Exits non-zero if anything fails —
#    safe to gate on.
#    egpt-infra is not part of the dev venv; it validates in its own CI job.
#
#    Usage: ./scripts/validate.sh
#
############################################################################

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${CURR_DIR}")"

# Colors
ORANGE='\033[38;5;208m'
GREEN='\033[32m'
RED='\033[31m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

# Fall back to the dev venv tools when not already on PATH
if ! command -v ruff &> /dev/null && [[ -x "${REPO_ROOT}/.venv/bin/ruff" ]]; then
    export PATH="${REPO_ROOT}/.venv/bin:${PATH}"
fi
for tool in ruff mypy; do
    if ! command -v "${tool}" &> /dev/null; then
        echo "    ${tool} not found. Run ./scripts/dev_setup.sh first."
        exit 1
    fi
done

echo ""
echo -e "    ${BOLD}EGPT Validate${NC}"

FAILURES=()
CURRENT_TARGET=""
START=${SECONDS}

target() {
    CURRENT_TARGET="$1"
    echo ""
    echo -e "    ${ORANGE}${CURRENT_TARGET}${NC}"
}

run_step() {
    local label="$1"
    shift
    local output
    if output="$("$@" 2>&1)"; then
        local summary
        summary="$(printf '%s' "${output}" | tail -n 1)"
        echo -e "    ${GREEN}✓${NC} ${label}${summary:+ ${DIM}— ${summary}${NC}}"
    else
        echo -e "    ${RED}✗ ${label}${NC}"
        printf '%s\n' "${output}" | sed 's/^/      /'
        FAILURES+=("${CURRENT_TARGET}: ${label}")
    fi
}

target "egpt"
run_step "ruff check" ruff check "${REPO_ROOT}/libs/agno"
run_step "mypy" mypy "${REPO_ROOT}/libs/agno" --config-file "${REPO_ROOT}/libs/agno/pyproject.toml"

target "egptctl"
run_step "ruff check" ruff check "${REPO_ROOT}/libs/egptctl"
run_step "mypy" mypy "${REPO_ROOT}/libs/egptctl/egptctl" --config-file "${REPO_ROOT}/libs/egptctl/pyproject.toml"

target "cookbook"
run_step "ruff check" ruff check "${REPO_ROOT}/cookbook"
run_step "pattern check" python3 "${REPO_ROOT}/cookbook/scripts/check_cookbook_pattern.py" --base-dir "${REPO_ROOT}/cookbook/00_quickstart"
run_step "import check" python3 "${REPO_ROOT}/cookbook/scripts/check_cookbook_imports.py" --base-dir "${REPO_ROOT}/cookbook" --lib-root "${REPO_ROOT}/libs/agno"

target "product"
run_step "ruff check" ruff check "${REPO_ROOT}/product"
# MYPYPATH: egpt is installed editable through an import-hook finder that mypy cannot follow,
# so the framework's source tree is named explicitly. Without it every `egpt.*` import in
# product/ is "import-not-found" and nothing behind it is checked.
run_step "mypy" env MYPYPATH="${REPO_ROOT}/libs/agno" mypy "${REPO_ROOT}/product/egpt_retail" --config-file "${REPO_ROOT}/product/pyproject.toml"
run_step "api contract" python3 "${REPO_ROOT}/scripts/check_api_contract.py"

target "repo"
run_step "brand audit" python3 "${REPO_ROOT}/scripts/brand_audit.py"

ELAPSED=$((SECONDS - START))
echo ""
if [[ ${#FAILURES[@]} -eq 0 ]]; then
    echo -e "    ${BOLD}Validated in ${ELAPSED}s.${NC}"
    echo ""
else
    echo -e "    ${BOLD}${RED}Failed:${NC} ${FAILURES[*]}"
    echo ""
    exit 1
fi
