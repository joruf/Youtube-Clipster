#!/bin/bash

# Loresoft Youtube Clipster - Linux
#
# Author: Joachim Ruf, Loresoft.de
# License: GPLv3 — Der Name des Autors muss bei Veröffentlichung und Veränderung genannt werden.

# --- Required ---
# For X11 Desktop
# sudo apt update && sudo apt install xclip
#
# For Wayland Display Server Protocol
# sudo apt update && sudo apt install wl-clipboard
#
# For "You are not a robot" queries
# pip install -U yt-dlp
#
# For Audio Extraction
# sudo apt update && sudo apt install ffmpeg
# 
# For GUI
# sudo apt install zenity

# --- Distribution Compatibility ---
# This script is primarily designed for Debian-based Linux distributions
# due to its reliance on the 'apt' package manager for system dependencies.
# It should work well on desktop environments of the following:
#
# - Ubuntu (and its official flavors like Kubuntu, Xubuntu, Lubuntu, MATE, Budgie)
# - Linux Mint (Cinnamon, MATE, XFCE editions, and LMDE)
# - Debian (any desktop installation)
# - Pop!_OS
# - Zorin OS
# - Elementary OS
# - MX Linux
# - Kali Linux (though specialized, it's Debian-based)
# - Parrot OS (also specialized, but Debian-based)

# --- Info ---
# If too many downloads are performed consecutively, Google often interrupts with a "verify you are not a bot" query.
# When this message appears, the IP address must be renewed.


# --- CONFIGURATION ---
declare -A MESSAGES
LANG_CHOICE="EN"                # Select language: DE | EN
OPEN_NEMO=false					# Open target folder when finished
INTERVAL_TIME_SEC="2"			# Main loop interval time
DOWNLOAD_DIR="$HOME/Downloads"	# mp3|mp4 download directory 
INSTALL_DIR="$HOME/.local/share/YoutubeClipster"
YTDLP_BIN="$INSTALL_DIR/yt-dlp"
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# General
APP_NAME="LORESOFT YOUTUBE CLIPSTER"
APP_VERSION="v1.02"
APP_TITLE="$APP_NAME - $APP_VERSION"


# --- LOAD LANGUAGE TEXTS ---
load_language() {
    case "$LANG_CHOICE" in
        DE)
            # --- GERMAN ---            
            # Console messages
            MESSAGES["separator"]="=================================================="
            MESSAGES["waiting_for_link"]="⌛ Warte auf YouTube-Link in der Zwischenablage..."
            MESSAGES["started"]="✅ Loresoft Youtube Clipster gestartet. Youtube-Link kopieren um Download zu starten."
            MESSAGES["interval_label"]="INTERVALL_ZEIT_SEK"
            MESSAGES["download_dir_label"]="DOWNLOAD_VERZ"
            MESSAGES["install_dir_label"]="INSTALLATIONS_VERZ"
            MESSAGES["ytdlp_bin_label"]="YTDLP_BIN"
            MESSAGES["user_agent_label"]="USER_AGENT"
            MESSAGES["lang_choice_label"]="SPRACHE"
            MESSAGES["debug_prefix"]="[DEBUG]"
            MESSAGES["opening_nemo"]="📂 Öffne Nemo..."
            MESSAGES["lockfile_removed"]="🔓 Lockfile entfernt."
            MESSAGES["only_one_instance"]="❌ Das Programm läuft bereits. Nur eine Instanz erlaubt."
            MESSAGES["orphaned_lock"]="⚠️ Verwaiste Lock-Datei gefunden. Entferne sie..."
            MESSAGES["lock_created"]="🔒 Lock-Datei erstellt."
            MESSAGES["install_error"]="❌ Fehler bei der Installation von '%s'.\nDas Programm wird beendet."
            MESSAGES["zenity_install_error"]="❌ Fehler bei der Installation von 'zenity'. GUI-Meldungen nicht möglich."
            MESSAGES["python_package_error"]="❌ Fehler bei der Installation von Python-Paket '%s'.\nDas Programm wird beendet."
            MESSAGES["checking_dependencies"]="🔍 Überprüfe benötigte Programme..."
            MESSAGES["dependencies_ok"]="✅ Alle benötigten Abhängigkeiten sind installiert."
            MESSAGES["link_received"]="⬇️ Youtube-Link erhalten, Prozess wird vorbereitet..."
            MESSAGES["clip_invalid"]="❌ Keine gültige YouTube-Adresse in der Zwischenablage gefunden."
            MESSAGES["clip_already_canceled"]="⚠️ Dieser Link wurde zuvor abgebrochen."
            MESSAGES["download_dir_not_found"]="❌ Download-Verzeichnis %s nicht gefunden."
            MESSAGES["starting_download"]="⬇️ Starte Download als %s: %s"
            MESSAGES["download_complete"]="✅ Download abgeschlossen: %s (%s) in %s"
            MESSAGES["download_error"]="❌ Fehler beim Download: %s (%s)"
            
            # Zenity dialogs
            MESSAGES["zenity_format_title"]="YouTube Clipster"
            MESSAGES["zenity_format_text_prefix"]="Format wählen für:"
            MESSAGES["zenity_format_col_select"]="Auswahl"
            MESSAGES["zenity_format_col_format"]="Format"
            MESSAGES["zenity_format_mp3"]="mp3"
            MESSAGES["zenity_format_mp4"]="mp4"
            MESSAGES["selection_column"]="Auswahl"
            MESSAGES["format_column"]="Format"
            MESSAGES["no_format_selected"]="❌ Kein Format ausgewählt. Download abgebrochen."
            
            # Progress display
            MESSAGES["progress_title"]="Loresoft YouTube Clipster"
            MESSAGES["progress_text_prefix"]="Verarbeite:"
            MESSAGES["progress_downloading"]="⬇️ Download..."
            MESSAGES["progress_converting_prefix"]="🔄 Konvertiere zu"
            MESSAGES["progress_converting_suffix"]="..."
            MESSAGES["progress_complete_prefix"]="✅ Fertig!"
            MESSAGES["progress_complete_suffix"]="gespeichert."
            
            # Fallback text
            MESSAGES["fallback_title"]="Video"
            MESSAGES["unknown_title"]="Unbekannter Titel"
            MESSAGES["download_title"]="Download: %s"
            ;;
            
        EN)
            # --- ENGLISH ---            
            # Console messages
            MESSAGES["separator"]="=================================================="
            MESSAGES["waiting_for_link"]="⌛ Waiting for YouTube link in clipboard..."
            MESSAGES["started"]="✅ Loresoft Youtube Clipster started. Copy YouTube link to start download."
            MESSAGES["interval_label"]="INTERVAL_TIME_SEC"
            MESSAGES["download_dir_label"]="DOWNLOAD_DIR"
            MESSAGES["install_dir_label"]="INSTALL_DIR"
            MESSAGES["ytdlp_bin_label"]="YTDLP_BIN"
            MESSAGES["user_agent_label"]="USER_AGENT"
            MESSAGES["lang_choice_label"]="LANGUAGE"
            MESSAGES["debug_prefix"]="[DEBUG]"
            MESSAGES["opening_nemo"]="📂 Opening Nemo..."
            MESSAGES["lockfile_removed"]="🔓 Lockfile removed."
            MESSAGES["only_one_instance"]="❌ Program is already running. Only one instance allowed."
            MESSAGES["orphaned_lock"]="⚠️ Orphaned lock file found. Removing it..."
            MESSAGES["lock_created"]="🔒 Lock file created."
            MESSAGES["install_error"]="❌ Error installing '%s'.\nProgram will exit."
            MESSAGES["zenity_install_error"]="❌ Error installing 'zenity'. GUI messages not possible."
            MESSAGES["python_package_error"]="❌ Error installing Python package '%s'.\nProgram will exit."
            MESSAGES["checking_dependencies"]="🔍 Checking required programs..."
            MESSAGES["dependencies_ok"]="✅ All required dependencies are installed."
            MESSAGES["link_received"]="⬇️ YouTube link received, process preparing..."
            MESSAGES["clip_invalid"]="❌ No valid YouTube link found in clipboard."
            MESSAGES["clip_already_canceled"]="⚠️ This link was previously canceled."
            MESSAGES["download_dir_not_found"]="❌ Download directory %s not found."
            MESSAGES["starting_download"]="⬇️ Starting download as %s: %s"
            MESSAGES["download_complete"]="✅ Download complete: %s (%s) in %s"
            MESSAGES["download_error"]="❌ Error during download: %s (%s)"
            
            # Zenity dialogs
            MESSAGES["zenity_format_title"]="YouTube Clipster"
            MESSAGES["zenity_format_text_prefix"]="Choose format for:"
            MESSAGES["zenity_format_col_select"]="Selection"
            MESSAGES["zenity_format_col_format"]="Format"
            MESSAGES["zenity_format_mp3"]="mp3"
            MESSAGES["zenity_format_mp4"]="mp4"
            MESSAGES["selection_column"]="Selection"
            MESSAGES["format_column"]="Format"
            MESSAGES["no_format_selected"]="❌ No format selected. Download canceled."
            
            # Progress display
            MESSAGES["progress_title"]="Loresoft YouTube Clipster"
            MESSAGES["progress_text_prefix"]="Processing:"
            MESSAGES["progress_downloading"]="⬇️ Downloading..."
            MESSAGES["progress_converting_prefix"]="🔄 Converting to"
            MESSAGES["progress_converting_suffix"]="..."
            MESSAGES["progress_complete_prefix"]="✅ Complete!"
            MESSAGES["progress_complete_suffix"]="saved."
            
            # Fallback text
            MESSAGES["fallback_title"]="Video"
            MESSAGES["unknown_title"]="Unknown Title"
            MESSAGES["download_title"]="Download: %s"
            ;;
            
        *)
            echo "ERROR: Unknown language '$LANG_CHOICE'. Using default (DE)."
            LANG_CHOICE="DE"
            load_language
            return
            ;;
    esac
}

# Cleanup function to remove lockfile
cleanup_lockfile() {
    if [ -f "$LOCKFILE" ]; then
        rm -f "$LOCKFILE"
        echo "${MESSAGES[lockfile_removed]}"
    fi
    # Beendet auch alle Hintergrundprozesse, die vom Skript gestartet wurden
    kill $(jobs -p) 2>/dev/null
    exit
}

# Function to install missing dependencies
check_and_install() {
    local cmd="$1"
    local pkg="$2"
    local method="$3"   # apt or pip
    
    if ! command -v "$cmd" &>/dev/null; then
        if [[ "$method" == "apt" ]]; then
            echo "Installing $pkg via apt..."
            sudo apt update
            if ! sudo apt install -y "$pkg"; then
                zenity --error --text="$(printf "${MESSAGES[install_error]}" "$pkg")"
                exit 1
            fi
        elif [[ "$method" == "pip" ]]; then
            echo "Installing $pkg via pip..."
            if ! pip install -U "$pkg"; then
                zenity --error --text="$(printf "${MESSAGES[python_package_error]}" "$pkg")"
                exit 1
            fi
        fi
    fi
}

# Robust link detection
get_clip() {
    local CLIP_DATA
    CLIP_DATA=$( (wl-paste || xclip -o -selection clipboard) 2>/dev/null)
    echo "$CLIP_DATA" | grep -oE "https://(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}|https://youtu\.be/[a-zA-Z0-9_-]{11}" | head -n 1
}



# --- LOCKFILE MANAGEMENT ---
# Prevent multiple instances with lockfile
LOCKFILE="$(pwd)/youtube-clipster.lock"

# Load language
load_language

# Register cleanup function for all exit scenarios
# This ensures lockfile removal even on crash, CTRL+C, or abnormal termination
trap cleanup_lockfile EXIT INT TERM HUP QUIT
# Extended trap for all common abort signals
trap cleanup_lockfile SIGINT SIGTERM EXIT SIGHUP


if [ -f "$LOCKFILE" ]; then
    OLDPID=$(cat "$LOCKFILE")
    if ps -p "$OLDPID" > /dev/null 2>&1; then
        echo "${MESSAGES[only_one_instance]} PID: $OLDPID"
        zenity --error --text="${MESSAGES[only_one_instance]}"
        exit 1
    else
        echo "${MESSAGES[orphaned_lock]}"
        rm -f "$LOCKFILE"
    fi
fi

echo $$ > "$LOCKFILE"
echo "${MESSAGES[lock_created]}"

# --- CREATE DIRECTORIES ---
mkdir -p "$DOWNLOAD_DIR" "$INSTALL_DIR"

# --- DISPLAY VARIABLES (CONSOLE) ---
echo "${MESSAGES[separator]}"
echo "   $APP_TITLE"
echo "${MESSAGES[separator]}"
echo "${MESSAGES[interval_label]} = $INTERVAL_TIME_SEC"
echo "${MESSAGES[download_dir_label]}      = $DOWNLOAD_DIR"
echo "${MESSAGES[install_dir_label]}       = $INSTALL_DIR"
echo "${MESSAGES[ytdlp_bin_label]}         = $YTDLP_BIN"
echo "${MESSAGES[user_agent_label]}        = $USER_AGENT"
echo "${MESSAGES[lang_choice_label]}       = $LANG_CHOICE"


# --- DEPENDENCY CHECK AND AUTO-INSTALL ---
echo "${MESSAGES[checking_dependencies]}"
# Check for clipboard tools (Wayland or X11)
if ! command -v wl-paste &>/dev/null && ! command -v xclip &>/dev/null; then
    # Detect display server protocol
    if [[ "$XDG_SESSION_TYPE" == "wayland" ]]; then
        check_and_install "wl-paste" "wl-clipboard" "apt"
    else
        check_and_install "xclip" "xclip" "apt"
    fi
fi

# Check for zenity (GUI dialogs)
check_and_install "zenity" "zenity" "apt"

# Check for ffmpeg (audio/video conversion)
check_and_install "ffmpeg" "ffmpeg" "apt"

# Check for yt-dlp (YouTube downloader)
check_and_install "yt-dlp" "yt-dlp" "pip"


# --- INITIALIZATION ---
# Ignore current clipboard content at startup
LAST_CLIP=$(get_clip)
CANCELED_CLIP=""
echo ""
echo "${MESSAGES[started]}"
echo "${MESSAGES[separator]}"

# Update check: Download yt-dlp binary if not present (fallback if pip install failed)
# Priority: use system yt-dlp if available, otherwise use local binary
if command -v yt-dlp &>/dev/null; then
    YTDLP_BIN="yt-dlp"
elif [[ ! -f "$YTDLP_BIN" ]]; then
    echo "Downloading yt-dlp binary as fallback..."
    curl -L "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp" -o "$YTDLP_BIN"
    chmod +x "$YTDLP_BIN"
fi

# --- MAIN LOOP ---
while true; do
  sleep "$INTERVAL_TIME_SEC"
  CLIP=$(get_clip)
  
  # Only process if new link detected
  if [[ -n "$CLIP" && "$CLIP" != "$LAST_CLIP" && "$CLIP" != "$CANCELED_CLIP" ]]; then
    echo "$CLIP"
    
    # Show immediate notification that link was detected
    (
      echo "0"
      echo "# ${MESSAGES[link_received]}"
      
      # Get video title in background
      TITLE=$("$YTDLP_BIN" --no-warnings --get-title "$CLIP" 2>/dev/null)
      SAFE_TITLE=$(echo "${TITLE:-${MESSAGES[fallback_title]}}" | sed 's/[^a-zA-Z0-9._ -]/ /g')
      
      echo "100"
      sleep 0.5
    ) | zenity --progress \
      --title="${MESSAGES[progress_title]}" \
      --text="${MESSAGES[progress_text_prefix]} YouTube Link" \
      --auto-close \
      --no-cancel \
      --width=400 \
      --pulsate 2>/dev/null
    
    # Get video title if not already retrieved
    if [[ -z "$SAFE_TITLE" ]]; then
        TITLE=$("$YTDLP_BIN" --no-warnings --get-title "$CLIP" 2>/dev/null)
        SAFE_TITLE=$(echo "${TITLE:-${MESSAGES[fallback_title]}}" | sed 's/[^a-zA-Z0-9._ -]/ /g')
    fi
    
    # Format selection dialog
    FORMAT=$(zenity --list \
      --title="${MESSAGES[zenity_format_title]}" \
      --text="${MESSAGES[zenity_format_text_prefix]}\n$SAFE_TITLE" \
      --radiolist \
      --column="${MESSAGES[zenity_format_col_select]}" \
      --column="${MESSAGES[zenity_format_col_format]}" \
      TRUE "${MESSAGES[zenity_format_mp3]}" \
      FALSE "${MESSAGES[zenity_format_mp4]}" \
      2>/dev/null)
    
    # Abort on ESC or Cancel
    if [[ -z "$FORMAT" ]]; then 
        CANCELED_CLIP="$CLIP"
        continue 
    fi
    
    # Change to download directory
    cd "$DOWNLOAD_DIR" || exit 1
    
    # Download process with progress display
    (
      echo "# ${MESSAGES[progress_downloading]}"
      echo "5"
      
      # Assemble command based on format
      if [[ "$FORMAT" == "${MESSAGES[zenity_format_mp3]}" ]]; then
          CMD=("$YTDLP_BIN" "--newline" "--restrict-filenames" "-x" "--audio-format" "mp3" "--audio-quality" "0" "$CLIP")
      else
          CMD=("$YTDLP_BIN" "--newline" "--restrict-filenames" "-f" "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b" "--merge-output-format" "mp4" "$CLIP")
      fi
      
      # Execute download and parse progress
      "${CMD[@]}" 2>&1 | while read -r line; do
          echo "${MESSAGES[debug_prefix]} $line" >&2
          
          # Extract progress percentage
          if [[ "$line" =~ ([0-9.]+)% ]]; then
              PERCENT=$(echo "${BASH_REMATCH[1]}" | cut -d'.' -f1)
              if [ "$PERCENT" -lt 99 ]; then echo "$PERCENT"; fi
          fi
          
          # Detect conversion phase
          if [[ "$line" == *"[ExtractAudio]"* || "$line" == *"[Merger]"* || "$line" == *"[VideoConvertor]"* ]]; then
              FORMAT_UPPER=$(echo "$FORMAT" | tr '[:lower:]' '[:upper:]')
              echo "# ${MESSAGES[progress_converting_prefix]} ${FORMAT_UPPER}${MESSAGES[progress_converting_suffix]}"
              echo "50"
          fi
      done
      
      # Completion
      echo "100"
      FORMAT_UPPER=$(echo "$FORMAT" | tr '[:lower:]' '[:upper:]')
      echo "# ${MESSAGES[progress_complete_prefix]} ${FORMAT_UPPER} ${MESSAGES[progress_complete_suffix]}"
      sleep 2
      
    ) | zenity --progress \
      --title="${MESSAGES[progress_title]}" \
      --text="${MESSAGES[progress_text_prefix]} $SAFE_TITLE" \
      --auto-close \
      --width=500
    
    # Optional: Open file manager
    if [ "$OPEN_NEMO" = true ]; then
        echo "${MESSAGES[debug_prefix]} ${MESSAGES[opening_nemo]}"
        nemo "$DOWNLOAD_DIR" &
    fi
    
    # Update status
    LAST_CLIP="$CLIP"
    CANCELED_CLIP=""
  fi
done
