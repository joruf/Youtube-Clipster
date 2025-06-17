#!/bin/bash

# Loresoft Youtube Clipster
# v1.0
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


# Define language strings
declare -A MESSAGES


# Configuration
INTERVAL_TIME_SEC="2"
DOWNLOAD_DIR="$HOME/Downloads"
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LAST_CLIP=""
CANCELED_CLIP=""
LANG_CHOICE="DE" # Select language DE|EN
mkdir -p "$DOWNLOAD_DIR"

# Language pack
if [[ "$LANG_CHOICE" == "DE" ]]; then
    MESSAGES["install_error"]="❌ Fehler bei der Installation von '%s'.\nDas Programm wird beendet."
    MESSAGES["zenity_install_error"]="❌ Fehler bei der Installation von 'zenity'. GUI-Meldungen nicht möglich."
    MESSAGES["python_package_error"]="❌ Fehler bei der Installation von Python-Paket '%s'.\nDas Programm wird beendet."
	MESSAGES["started"]="✅ Loresoft Youtube Clipster gestartet. Youtube-Link kopieren um Download zu starten."
    MESSAGES["link_received"]="⬇️ Youtube-Link erhalten, Prozess wird vorbereitet..."
    MESSAGES["unknown_title"]="Unbekannter Titel"
    MESSAGES["download_title"]="Download: %s"
    MESSAGES["selection_column"]="Auswahl"
    MESSAGES["format_column"]="Format"
    MESSAGES["no_format_selected"]="❌ Kein Format ausgewählt. Download abgebrochen."
    MESSAGES["download_dir_not_found"]="❌ Download-Verzeichnis %s nicht gefunden."
    MESSAGES["starting_download"]="⬇️ Starte Download als %s: %s"
    MESSAGES["download_complete"]="✅ Download abgeschlossen: %s (%s) in %s"
    MESSAGES["download_error"]="❌ Fehler beim Download: %s (%s)"
elif [[ "$LANG_CHOICE" == "EN" ]]; then
    MESSAGES["install_error"]="❌ Error installing '%s'.\nProgram will exit."
    MESSAGES["zenity_install_error"]="❌ Error installing 'zenity'. GUI messages not possible."
    MESSAGES["python_package_error"]="❌ Error installing Python package '%s'.\nProgram will exit."
	MESSAGES["started"]="✅ Loresoft Youtube Clipster started. Copy YouTube link to start download."
	MESSAGES["link_received"]="⬇️ YouTube link received, process preparing..."
    MESSAGES["unknown_title"]="Unknown Title"
    MESSAGES["download_title"]="Download: %s"
    MESSAGES["selection_column"]="Selection"
    MESSAGES["format_column"]="Format"
    MESSAGES["no_format_selected"]="❌ No format selected. Download canceled."
    MESSAGES["download_dir_not_found"]="❌ Download directory %s not found."
    MESSAGES["starting_download"]="⬇️ Starting download as %s: %s"
    MESSAGES["download_complete"]="✅ Download complete: %s (%s) in %s"
    MESSAGES["download_error"]="❌ Error during download: %s (%s)"
fi





# Function to install missing dependencies
check_and_install() {
  local cmd="$1"
  local pkg="$2"
  local method="$3"   # apt or pip

  if ! command -v "$cmd" &>/dev/null; then
    if [[ "$method" == "apt" ]]; then
      sudo apt update
      if ! sudo apt install -y "$pkg"; then
        zenity --error --text="$(printf "${MESSAGES["install_error"]}" "$pkg")"
        exit 1
      fi
    elif [[ "$method" == "pip" ]]; then
      if ! pip install -U "$pkg"; then
        zenity --error --text="$(printf "${MESSAGES["python_package_error"]}" "$pkg")"
        exit 1
      fi
    fi
  fi
}

# Ensure Zenity is installed
if ! command -v zenity &>/dev/null; then
  sudo apt update
  if ! sudo apt install -y zenity; then
    echo "${MESSAGES["zenity_install_error"]}"
    exit 1
  fi
fi

# Install required tools
if [[ "$XDG_SESSION_TYPE" == "wayland" ]]; then
  check_and_install "wl-paste" "wl-clipboard" "apt"
else
  check_and_install "xclip" "xclip" "apt"
fi

check_and_install "yt-dlp" "yt-dlp" "pip"
check_and_install "ffmpeg" "ffmpeg" "apt"
check_and_install "zenity" "zenity" "apt"

zenity --notification --text="${MESSAGES["started"]}"





# Main loop
while true; do
  sleep "$INTERVAL_TIME_SEC"
  CLIP=$(xclip -o -selection clipboard 2>/dev/null)

  if [[ -n "$CLIP" && "$CLIP" != "$LAST_CLIP" && "$CLIP" != "$CANCELED_CLIP" ]]; then
    if [[ "$CLIP" =~ ^https?://(www\.)?(youtube\.com|youtu\.be)/ ]]; then
      zenity --notification --text="${MESSAGES["link_received"]}"

      # Get title
      TITLE=$(yt-dlp --user-agent "$USER_AGENT" --no-playlist --skip-download --no-warnings --get-title "$CLIP" 2>/dev/null)
      TITLE="${TITLE:-${MESSAGES["unknown_title"]}}"

      # Format selection MP3 or MP4
      FORMAT=$(zenity --list --title="$(printf "${MESSAGES["download_title"]}" "$TITLE")" --radiolist \
        --column="${MESSAGES["selection_column"]}" --column="${MESSAGES["format_column"]}" \
        TRUE mp3 FALSE mp4 2>/dev/null)

      if [[ -z "$FORMAT" ]]; then
        zenity --notification --text="${MESSAGES["no_format_selected"]}"
        CANCELED_CLIP="$CLIP"   # Mark URL as canceled
        continue
      fi

      cd "$DOWNLOAD_DIR" || {
        zenity --error --text="$(printf "${MESSAGES["download_dir_not_found"]}" "$DOWNLOAD_DIR")"
        exit 1
      }

      zenity --notification --text="$(printf "${MESSAGES["starting_download"]}" "$FORMAT" "$TITLE")"

      if [[ "$FORMAT" == "mp3" ]]; then
        yt-dlp --user-agent "$USER_AGENT" --no-playlist -x --audio-format mp3 "$CLIP"
      else
        yt-dlp --user-agent "$USER_AGENT" --no-playlist -f bestvideo+bestaudio --merge-output-format mp4 "$CLIP"
      fi

      RET=$?

      if [[ $RET -eq 0 ]]; then
        zenity --notification --text="$(printf "${MESSAGES["download_complete"]}" "$TITLE" "$FORMAT" "$DOWNLOAD_DIR")"
      else
        zenity --notification --text="$(printf "${MESSAGES["download_error"]}" "$TITLE" "$FORMAT")"
      fi

      LAST_CLIP="$CLIP"   # Set only after successful download
      CANCELED_CLIP=""    # Reset to allow new URLs
    fi
  fi
done
