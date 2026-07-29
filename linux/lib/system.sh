#!/bin/bash

# Central logging function - ALWAYS outputs to stderr to keep stdout clean for GUI/Zenity
log_message() {
    local level="$1"   # INFO, DEBUG, WARN, ERROR
    local message="$2"
    local timestamp
    timestamp=$(date +"%Y-%m-%d %H:%M:%S")
    
    case "$level" in
        "INFO")  echo -e "[$timestamp] \e[32m[INFO]\e[0m  $message" >&2 ;;
        "DEBUG") echo -e "[$timestamp] \e[34m[DEBUG]\e[0m $message" >&2 ;;
        "WARN")  echo -e "[$timestamp] \e[33m[WARN]\e[0m  $message" >&2 ;;
        "ERROR") echo -e "[$timestamp] \e[31m[ERROR]\e[0m $message" >&2 ;;
        *)       echo -e "[$timestamp] [$level] $message" >&2 ;;
    esac
}

# Load localized environment string config file
load_language() {
    local lang_file="locales/${LANG_CHOICE,,}.cfg"
    if [[ -f "$lang_file" ]]; then
        source "$lang_file"
        log_message "DEBUG" "Language file loaded: $lang_file"
    else
        source "locales/de.cfg"
        log_message "WARN" "Language file not found. Fallback to de.cfg"
    fi
}

# Perform cleanup procedures upon script exit or termination signals
cleanup_lockfile() {
    if [ -f "$LOCKFILE" ]; then
        rm -f "$LOCKFILE"
        log_message "DEBUG" "Lockfile removed."
    fi
    kill $(jobs -p) 2>/dev/null
    exit
}

# Ensure single-instance execution via lockfile validation
manage_lockfile() {
    LOCKFILE="$(pwd)/youtube-clipster.lock"
    log_message "DEBUG" "Checking instance lockfile: $LOCKFILE"
    if [ -f "$LOCKFILE" ]; then
        local old_pid
        old_pid=$(cat "$LOCKFILE")
        if ps -p "$old_pid" > /dev/null 2>&1; then
            log_message "ERROR" "Program is already running. Only one instance allowed. PID: $old_pid"
            zenity --error --text="${MESSAGES[only_one_instance]}" 2>/dev/null
            exit 1
        else
            log_message "WARN" "Orphaned lockfile found. Removing it..."
            rm -f "$LOCKFILE"
        fi
    fi
    echo $$ > "$LOCKFILE"
    log_message "DEBUG" "Lockfile created successfully. (PID: $$)"
}

# Return yt-dlp --js-runtimes arguments for the first available JavaScript runtime
ytdlp_js_runtime_args() {
    local -n _out=$1
    _out=()
    if command -v qjs &>/dev/null; then
        _out=(--js-runtimes quickjs)
    elif command -v node &>/dev/null; then
        _out=(--js-runtimes node)
    elif command -v deno &>/dev/null; then
        _out=(--js-runtimes deno)
    fi
}

# Fetch a YouTube watch URL from the OS clipboard and normalize it.
# Accepts playlist/radio/share query orders, music./m. hosts, youtu.be and shorts.
get_clip() {
    local clip_data
    clip_data=$( (wl-paste || xclip -o -selection clipboard) 2>/dev/null)
    [[ -z "$clip_data" ]] && return 0

    python3 -c '
import re
import sys

text = sys.stdin.read()
if "youtu" not in text.lower():
    sys.exit(0)

patterns = [
    r"(?:youtu\.be/|youtube\.com/(?:shorts|embed|live)/)([A-Za-z0-9_-]{11})",
    r"youtube\.com/watch\?[^\s]*?\bv=([A-Za-z0-9_-]{11})",
    r"[?&]v=([A-Za-z0-9_-]{11})",
]
for pattern in patterns:
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        print(f"https://www.youtube.com/watch?v={match.group(1)}")
        break
' <<< "$clip_data"
}
