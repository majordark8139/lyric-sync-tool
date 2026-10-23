#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
LIB_DIR="${SCRIPT_DIR}/lib"

echo "=== Installing Lyric Sync Tool ==="

# Check python3
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not installed." >&2
    exit 1
fi

# ── System Dependency Auto-Provisioner & Local Bundling ──
mkdir -p "${LIB_DIR}"

is_xcb_cursor_installed() {
    # Check ldconfig cache
    if ldconfig -p 2>/dev/null | grep -q "libxcb-cursor\.so\.0"; then
        return 0
    fi
    # Check common system library locations
    for p in /usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0* \
             /usr/lib64/libxcb-cursor.so.0* \
             /usr/lib/libxcb-cursor.so.0* \
             /usr/lib/aarch64-linux-gnu/libxcb-cursor.so.0*; do
        if [ -e "$p" ]; then
            return 0
        fi
    done
    return 1
}

has_passwordless_sudo() {
    if command -v sudo &>/dev/null; then
        if sudo -n true 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

echo "Checking Qt6 XCB platform dependencies..."
if ! is_xcb_cursor_installed; then
    echo "libxcb-cursor is not installed on the system."
    INSTALLED_VIA_SUDO=false

    if has_passwordless_sudo; then
        echo "Passwordless sudo is available. Installing required desktop runtime packages..."
        if command -v apt-get &>/dev/null; then
            echo "Detected Debian/Ubuntu/Mint (apt-get)."
            if sudo DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::ForceIPv4=true install -y \
                libxcb-cursor0 libxkbcommon-x11-0 libgl1 libegl1 2>/dev/null || \
               sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
                libxcb-cursor0 libxkbcommon-x11-0 libgl1 libegl1; then
                INSTALLED_VIA_SUDO=true
            fi
        elif command -v dnf &>/dev/null; then
            echo "Detected Fedora (dnf)."
            if sudo dnf install -y xcb-util-cursor libxkbcommon-x11 mesa-libGL; then
                INSTALLED_VIA_SUDO=true
            fi
        elif command -v pacman &>/dev/null; then
            echo "Detected Arch Linux (pacman)."
            if sudo pacman -S --noconfirm xcb-util-cursor libxkbcommon-x11 mesa; then
                INSTALLED_VIA_SUDO=true
            fi
        fi
    else
        echo "Passwordless sudo is not available."
    fi

    # Fallback Local Bundling (for live USBs, non-sudo users, or offline setups)
    if [ "${INSTALLED_VIA_SUDO}" != "true" ] || ! is_xcb_cursor_installed; then
        echo "Fallback local bundling: extracting libxcb-cursor into ${LIB_DIR}..."
        if command -v apt-get &>/dev/null && command -v dpkg &>/dev/null; then
            TMP_DIR="$(mktemp -d)"
            (
                cd "${TMP_DIR}"
                if apt-get -o Acquire::ForceIPv4=true download libxcb-cursor0 2>/dev/null || \
                   apt-get download libxcb-cursor0 2>/dev/null || \
                   apt download libxcb-cursor0 2>/dev/null; then
                    dpkg -x ./*.deb extracted
                    find extracted -name "libxcb-cursor.so*" -exec cp -a {} "${LIB_DIR}/" \;
                    echo "Extracted libxcb-cursor into ${LIB_DIR}/ successfully."
                fi
            ) || true
            rm -rf "${TMP_DIR}"
        fi
    fi
else
    echo "Qt6 XCB platform dependency (libxcb-cursor) is already installed."
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
