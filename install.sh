#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

echo "=== Installing Lyric Sync Tool ==="

# Check python3
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not installed." >&2
    exit 1
fi

# Create virtual environment if not present
if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating virtual environment in ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
fi

# Upgrade pip and install requirements
echo "Installing dependencies from requirements.txt..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${SCRIPT_DIR}/requirements.txt"

# Make run.sh executable
chmod +x "${SCRIPT_DIR}/run.sh"

# Create desktop entry
DESKTOP_DIR="${HOME}/.local/share/applications"
mkdir -p "${DESKTOP_DIR}"
DESKTOP_FILE="${DESKTOP_DIR}/lyric-sync-tool.desktop"

echo "Creating desktop shortcut at ${DESKTOP_FILE}..."
cat <<EOF > "${DESKTOP_FILE}"
[Desktop Entry]
Version=1.0
Type=Application
Name=Lyric Sync Tool
GenericName=Lyrics Synchronizer
Comment=Offline Syllable-Synced Lyrics Manager
Exec=${SCRIPT_DIR}/run.sh
Path=${SCRIPT_DIR}
Terminal=false
Categories=AudioVideo;Audio;Player;
StartupNotify=true
EOF

chmod +x "${DESKTOP_FILE}"

echo "=== Installation Complete ==="
echo "You can launch the application with ./run.sh or via your desktop application launcher."
