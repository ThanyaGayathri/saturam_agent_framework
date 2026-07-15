#!/bin/bash

############################################################################
#
#    Agno Test Setup
#
#    Creates .venv with egptctl and agno[tests] installed in editable
#    mode: the full provider-SDK test surface used by the CI matrices.
#    For the day-to-day dev loop use ./scripts/dev_setup.sh instead.
#
#    Usage: ./scripts/test_setup.sh
#
############################################################################

set -e

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${CURR_DIR}")"
AGNO_DIR="${REPO_ROOT}/libs/agno"
EGPTCTL_DIR="${REPO_ROOT}/libs/egptctl"
VENV_DIR="${REPO_ROOT}/.venv"

# Colors
ORANGE='\033[38;5;208m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${ORANGE}"
cat << 'BANNER'
     █████╗  ██████╗ ███╗   ██╗ ██████╗
    ██╔══██╗██╔════╝ ████╗  ██║██╔═══██╗
    ███████║██║  ███╗██╔██╗ ██║██║   ██║
    ██╔══██║██║   ██║██║╚██╗██║██║   ██║
    ██║  ██║╚██████╔╝██║ ╚████║╚██████╔╝
    ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝
BANNER
echo -e "${NC}"
echo -e "    ${DIM}Test Setup${NC}"
echo ""

# Preflight
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "    Deactivate your current venv first."
    exit 1
fi

if ! command -v uv &> /dev/null; then
    echo "    uv not found. Install: https://docs.astral.sh/uv/"
    exit 1
fi

# Setup
echo -e "    ${DIM}Removing old environment...${NC}"
echo -e "    ${DIM}> rm -rf ${VENV_DIR}${NC}"
rm -rf "${VENV_DIR}"

echo ""
echo -e "    ${DIM}Creating Python 3.12 venv...${NC}"
echo -e "    ${DIM}> uv venv ${VENV_DIR} --python 3.12${NC}"
uv venv "${VENV_DIR}" --python 3.12 --quiet

# Install the separately distributed CLI and core library together for testing.
echo ""
echo -e "    ${DIM}Installing egptctl and agno[tests] in editable mode...${NC}"
echo -e "    ${DIM}> uv pip install -e libs/egptctl -e libs/agno[tests]${NC}"
VIRTUAL_ENV="${VENV_DIR}" uv pip install -e "${EGPTCTL_DIR}" -e "${AGNO_DIR}[tests]" --quiet

# Install brave-search without its dependencies: it hard-pins numpy<2 (and older
# httpx/pytest/tenacity), which would downgrade the resolved test environment and
# break numpy 2.x — and its 0.2.0 metadata does not parse under uv at all. Its
# runtime deps are already satisfied at newer versions.
echo -e "    ${DIM}> uv pip install brave-search --no-deps${NC}"
VIRTUAL_ENV="${VENV_DIR}" uv pip install brave-search --no-deps --quiet

echo ""
echo -e "    ${BOLD}Done.${NC}"
echo ""
echo -e "    ${DIM}Activate:${NC}  source .venv/bin/activate"
echo -e "    ${DIM}Test:${NC}      pytest libs/agno/tests"
echo ""
