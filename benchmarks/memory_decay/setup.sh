#!/bin/bash
# Dedicated venv for the episodic-decay decision harness (SM-R5).
# Self-contained; no framework deps, no external services.
set -e
CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "${CURR_DIR}/.venv"
"${CURR_DIR}/.venv/bin/python" -m pip install --quiet --upgrade pip
"${CURR_DIR}/.venv/bin/python" -m pip install --quiet -r "${CURR_DIR}/requirements.txt"
echo "Ready. Run: ./.venv/bin/python run_benchmark.py --seeds 5"
