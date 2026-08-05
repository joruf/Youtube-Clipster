#!/usr/bin/env bash
#
# Loresoft YouTube Clipster - Android setup, to be run inside Termux.
#
# Turns the checkout into something that behaves like an installed app: a
# launcher icon that starts the program and opens its interface, an optional
# start at boot, and the interface itself added to the home screen.
#
# Nothing is exposed to the network: on the phone the interface is reached over
# 127.0.0.1, which the phone's own browser can open and nothing outside the
# device can. The token still matters - every app on the phone can reach
# localhost too.
#
# Usage:  ./install-android.sh [--boot] [--no-widget]
#
# Author:  Joachim Ruf, Loresoft.de
# License: GPLv3

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WANT_BOOT=0
WANT_WIDGET=1

info()  { printf '\033[32m[INFO]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[33m[WARN]\033[0m  %s\n' "$*"; }
error() { printf '\033[31m[ERROR]\033[0m %s\n' "$*" >&2; }
step()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }

for argument in "$@"; do
    case "$argument" in
        --boot)       WANT_BOOT=1 ;;
        --no-widget)  WANT_WIDGET=0 ;;
        -h|--help)
            sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) error "Unknown option: $argument"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
step "Checking that this is Termux"
# ---------------------------------------------------------------------------
if [ -z "${PREFIX:-}" ] || [ "${PREFIX#*com.termux}" = "$PREFIX" ]; then
    error "This script belongs inside Termux on Android."
    error "On a desktop use ./install.sh instead."
    exit 1
fi
info "Termux detected: $PREFIX"
if [ -n "${TERMUX_VERSION:-}" ]; then
    info "Termux version: $TERMUX_VERSION"
fi

# ---------------------------------------------------------------------------
step "Installing the system packages"
# ---------------------------------------------------------------------------
# ffmpeg converts, mpv is optional and only used for playback on this device,
# termux-api provides the clipboard and the wake lock helpers.
PACKAGES="python ffmpeg termux-api"
info "pkg install $PACKAGES"
if ! pkg install -y $PACKAGES; then
    error "The packages could not be installed. Run 'pkg update' and try again."
    exit 1
fi
if ! pkg install -y mpv; then
    warn "mpv is unavailable - downloads still work, playing on this device does not."
fi

# ---------------------------------------------------------------------------
step "Installing the Python dependencies"
# ---------------------------------------------------------------------------
# No windows here, so the check skips tkinter, the tray and the clipboard tools.
if ! python "$SCRIPT_DIR/run.py" --check --headless; then
    error "The dependency check failed. The log is in ~/.local/share/YoutubeClipster/."
    exit 1
fi

# ---------------------------------------------------------------------------
step "Switching on the interface, for this device only"
# ---------------------------------------------------------------------------
python - "$SCRIPT_DIR" <<'PYTHON'
"""Enable remote control bound to loopback and make sure a token exists."""
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from clipster.config import Config
from clipster.webserver import new_token

config = Config.load()
config.remote_enabled = True
# Loopback: the browser on this very phone reaches it, nothing off-device does.
config.remote_bind = "127.0.0.1"
config.use_tray = False
if not config.remote_token:
    config.remote_token = new_token()
config.save()
print("Configuration: {0}".format(config.path))
print("Interface:     http://127.0.0.1:{0}/".format(config.remote_port))
PYTHON
if [ $? -ne 0 ]; then
    error "The configuration could not be written."
    exit 1
fi

# ---------------------------------------------------------------------------
step "Terms of use"
# ---------------------------------------------------------------------------
# Asked once, here, with the text on screen. The launcher never confirms them.
python - "$SCRIPT_DIR" <<'PYTHON'
"""Show the terms and accept them only on an explicit yes."""
import sys

sys.path.insert(0, sys.argv[1])

from clipster import i18n
from clipster.config import Config
from clipster.terms import TERMS_APP_VERSION, accept_app_terms, app_terms_accepted

config = Config.load()
if app_terms_accepted(config):
    print("Already accepted (version {0}).".format(config.terms_app_version))
    raise SystemExit(0)

messages = i18n.load(config.language or "en")
print()
print(messages["terms_app_title"])
print("-" * 60)
print(messages["terms_app_body"])
print("-" * 60)
print()
try:
    answer = input("Accept these terms (version {0})? [y/N] ".format(TERMS_APP_VERSION))
except (EOFError, KeyboardInterrupt):
    answer = ""
if answer.strip().lower() not in ("y", "yes", "j", "ja"):
    print("Not accepted - the program will refuse to start.")
    raise SystemExit(1)
accept_app_terms(config)
config.save()
print("Accepted and recorded in {0}.".format(config.path))
PYTHON
if [ $? -ne 0 ]; then
    error "Without accepting the terms the program cannot run."
    exit 1
fi

# ---------------------------------------------------------------------------
step "Creating the launcher"
# ---------------------------------------------------------------------------
LAUNCHER="$HOME/.shortcuts/Clipster"
if [ "$WANT_WIDGET" -eq 1 ]; then
    mkdir -p "$HOME/.shortcuts"
    cat > "$LAUNCHER" <<LAUNCH
#!/data/data/com.termux/files/usr/bin/bash
# Starts YouTube Clipster if it is not running, then opens its interface.
# Created by install-android.sh - safe to edit.
exec "$SCRIPT_DIR/tools/android/clipster-start" --open
LAUNCH
    chmod +x "$LAUNCHER"
    info "Launcher written to $LAUNCHER"
    info "Add the Termux:Widget widget to your home screen to get the icon."
else
    info "Skipped (--no-widget)."
fi

# ---------------------------------------------------------------------------
step "Start at boot"
# ---------------------------------------------------------------------------
if [ "$WANT_BOOT" -eq 1 ]; then
    mkdir -p "$HOME/.termux/boot"
    cat > "$HOME/.termux/boot/clipster" <<BOOT
#!/data/data/com.termux/files/usr/bin/bash
# Starts YouTube Clipster when the phone boots (needs the Termux:Boot app).
exec "$SCRIPT_DIR/tools/android/clipster-start"
BOOT
    chmod +x "$HOME/.termux/boot/clipster"
    info "Boot script written to $HOME/.termux/boot/clipster"
    info "Install the Termux:Boot app for it to take effect."
else
    info "Not enabled. Re-run with --boot if you want it."
fi

# ---------------------------------------------------------------------------
step "Done"
# ---------------------------------------------------------------------------
URL="$(python "$SCRIPT_DIR/tools/phone_link.py" --url 2>/dev/null || true)"
cat <<SUMMARY

Start it now:

    $SCRIPT_DIR/tools/android/clipster-start --open

That acquires a wake lock, starts the program without any window, and opens the
interface in your browser.

Then, in the browser: menu -> Add to home screen. From that point on you have an
icon that opens Clipster full screen, and Clipster appears in Android's share
menu - share a video from the YouTube app and it downloads here.

SUMMARY
if [ -n "$URL" ]; then
    printf 'The address, in case you need it by hand:\n    %s\n\n' "$URL"
fi
cat <<'NOTES'
Two things worth knowing:

  * Android stops background processes. The start script takes a wake lock, and
    you should also exempt Termux from battery optimisation in Android settings.
  * Copying a link cannot start a download on Android - the system forbids
    reading the clipboard in the background. Use the share menu instead.
NOTES
