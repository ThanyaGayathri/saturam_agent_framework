#!/bin/bash
# Dedicated venv for the event-segmentation decision harness (SM-R6).
# Self-contained; no framework deps. Offline candidates need no services or keys.
# The llm_boundary option optionally uses Azure OpenAI (Responses API) via a
# gitignored .env, or a local ollama SLM.
set -e
CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "${CURR_DIR}/.venv"
"${CURR_DIR}/.venv/bin/python" -m pip install --quiet --upgrade pip
"${CURR_DIR}/.venv/bin/python" -m pip install --quiet -r "${CURR_DIR}/requirements.txt"
echo "Ready. Run: ./.venv/bin/python run_benchmark.py --seeds 5"
