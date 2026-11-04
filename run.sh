#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python"

if [ ! -f "${VENV_PYTHON}" ]; then
    echo "Virtual environment not found at ${SCRIPT_DIR}/.venv"
    if [ -f "${SCRIPT_DIR}/install.sh" ]; then
        echo "Auto-running install.sh to set up dependencies..."
        bash "${SCRIPT_DIR}/install.sh"
    else
        echo "Please run ./install.sh first."
        exit 1
    fi
fi

# Export LD_LIBRARY_PATH with local bundling and venv support
export LD_LIBRARY_PATH="${SCRIPT_DIR}/lib:${SCRIPT_DIR}/.venv/lib:${LD_LIBRARY_PATH:-}"

# Export QT_QPA_PLATFORMTHEME with clean fallback
if [ -z "${QT_QPA_PLATFORMTHEME:-}" ]; then
    export QT_QPA_PLATFORMTHEME="gtk3"
fi

# Ensure project root is in PYTHONPATH
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

exec "${VENV_PYTHON}" "${SCRIPT_DIR}/gui/app.py" "$@"
