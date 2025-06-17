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

## Customization

You can configure the script by editing the following lines in `youtube_clipster.sh`:

- Set the language (choose EN for English or DE for German)
  ```bash
  LANG_CHOICE="EN"

- Set the download directory
  ```bash
  DOWNLOAD_DIR="$HOME/Downloads"

---

## Installation via GitHub

To install **Youtube Clipster** from this GitHub repository:

### Step 1: Clone the repository
```bash
git clone https://github.com/joruf/youtube-clipster.git
```

### Step 2: Change into the project directory
```bash
cd youtube-clipster
```

### Step 3: Make the script executable
```bash
chmod +x youtube_clipster.sh
```

### Step 4: Run it in the background
```bash
./youtube_clipster.sh &
```
