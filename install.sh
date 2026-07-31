#!/usr/bin/env bash
#
# Loresoft YouTube Clipster - Linux / macOS starter.
#
# Finds a suitable Python 3, installs it if necessary, and hands over to
# run.py, which installs every remaining dependency itself.
#
# Usage:  ./install.sh [--check] [--help] [any other run.py option]
#
# Author:  Joachim Ruf, Loresoft.de
# License: GPLv3

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENTRY="$SCRIPT_DIR/run.py"
MIN_MAJOR=3
MIN_MINOR=8

info()  { printf '\033[32m[INFO]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[33m[WARN]\033[0m  %s\n' "$*"; }
error() { printf '\033[31m[ERROR]\033[0m %s\n' "$*" >&2; }

# Return 0 when the given interpreter is new enough.
python_ok() {
    "$1" -c "import sys; sys.exit(0 if sys.version_info[:2] >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null
}

# Print the first usable interpreter found in PATH.
find_python() {
    local candidate
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

# Install Python through the distribution package manager.
install_python() {
    local sudo_cmd=""
    [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"

    if command -v apt-get >/dev/null 2>&1; then
        $sudo_cmd apt-get update && $sudo_cmd apt-get install -y python3 python3-venv python3-tk
    elif command -v dnf >/dev/null 2>&1; then
        $sudo_cmd dnf install -y python3 python3-tkinter
    elif command -v pacman >/dev/null 2>&1; then
        $sudo_cmd pacman -S --needed --noconfirm python tk
    elif command -v zypper >/dev/null 2>&1; then
        $sudo_cmd zypper --non-interactive install python3 python3-tk
    elif command -v brew >/dev/null 2>&1; then
        brew install python python-tk
    else
        return 1
    fi
}

if [ ! -f "$ENTRY" ]; then
    error "run.py not found next to this script ($SCRIPT_DIR)."
    exit 1
fi

PYTHON="$(find_python || true)"

if [ -z "${PYTHON:-}" ]; then
    warn "No Python >= $MIN_MAJOR.$MIN_MINOR found - trying to install it..."
    if ! install_python; then
        error "Python could not be installed automatically."
        error "Please install Python $MIN_MAJOR.$MIN_MINOR or newer and run this script again."
        exit 1
    fi
    PYTHON="$(find_python || true)"
fi

if [ -z "${PYTHON:-}" ]; then
    error "Python $MIN_MAJOR.$MIN_MINOR or newer is still not available."
    exit 1
fi

info "Using $PYTHON ($("$PYTHON" --version 2>&1))"
chmod +x "$ENTRY" 2>/dev/null || true

exec "$PYTHON" "$ENTRY" "$@"
