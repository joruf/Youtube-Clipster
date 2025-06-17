# Youtube Clipster

Loresoft Youtube Clipster is a simple Bash script to automatically download YouTube videos or audio by copying a YouTube link to your clipboard. It supports both X11 and Wayland desktop environments on Debian-based Linux distributions.

---

## Features

- Automatically detects YouTube links copied to clipboard
- Choose between downloading audio (mp3) or video (mp4)
- GUI dialogs via Zenity for user-friendly interaction
- Supports dependency installation (xclip/wl-clipboard, yt-dlp, ffmpeg, zenity)
- Works on popular Debian-based distros like Ubuntu, Mint, Debian, Pop!_OS, and more

---

## Requirements

- Debian-based Linux distribution
- Bash shell
- `xclip` (for X11) or `wl-clipboard` (for Wayland)
- `yt-dlp` (Python package)
- `ffmpeg`
- `zenity`

---

## Installation via GitHub

To install **Youtube Clipster** from this GitHub repository:

```bash
# Step 1: Clone the repository
git clone https://github.com/joruf/Youtube-Clipster.git

# Step 2: Change into the project directory
cd Youtube-Clipster

# Step 3: Make the script executable
chmod +x youtube-clipster.sh

# Step 4: Run it in the background
./youtube-clipster.sh &
