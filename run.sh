#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python"

if [ ! -f "${VENV_PYTHON}" ]; then
    echo "Virtual environment not found at ${SCRIPT_DIR}/.venv"
    echo "Please run ./install.sh first."
    exit 1
fi

export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"
exec "${VENV_PYTHON}" "${SCRIPT_DIR}/gui/app.py" "$@"
