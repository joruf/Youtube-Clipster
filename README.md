# YouTube Clipster

**Loresoft YouTube Clipster** is a lightweight tool that automatically downloads YouTube videos or audio by simply copying a YouTube link to your clipboard. Available for both **Linux** and **Windows**.

---

## Features

- 🎯 **Automatic Detection** - Monitors clipboard for YouTube links
- 🎵 **Format Selection** - Choose between audio (MP3) or video (MP4) download
- 🖥️ **GUI Dialogs** - User-friendly interface for interaction
- ⚙️ **Auto-Installation** - Automatically installs and updates required dependencies
- 🌍 **Multi-Language** - Supports English and German
- 🔒 **Single Instance** - Prevents multiple instances from running simultaneously

---

## Platform Support

### Linux
Designed for Debian-based distributions using the `apt` package manager:
- Ubuntu (all official flavors: Kubuntu, Xubuntu, Lubuntu, MATE, Budgie)
- Linux Mint (Cinnamon, MATE, XFCE, LMDE)
- Debian
- Pop!_OS
- Zorin OS
- Elementary OS
- MX Linux
- Kali Linux
- Parrot OS

Supports both **X11** and **Wayland** desktop environments.

### Windows
- Windows 10 or later
- PowerShell (included by default)

---

## Requirements

### Linux
The script automatically installs these dependencies if missing:
- `xclip` (for X11) or `wl-clipboard` (for Wayland)
- `yt-dlp` (Python package)
- `ffmpeg`
- `zenity`

### Windows
Dependencies are downloaded automatically:
- `yt-dlp.exe` (auto-downloaded from GitHub)
- `ffmpeg` (auto-downloaded)
- PowerShell (pre-installed on Windows 10+)

---

## Installation

### Linux Installation

#### Option 1: Via GitHub

```bash
# Clone the repository
git clone https://github.com/joruf/youtube-clipster.git

# Change into the project directory
cd youtube-clipster

# Make the script executable
chmod +x youtube_clipster.sh

# Run in the background
./youtube_clipster.sh &
```

#### Option 2: Quick Install

```bash
# Download the script
wget https://raw.githubusercontent.com/joruf/youtube-clipster/main/youtube_clipster.sh

# Make it executable
chmod +x youtube_clipster.sh

# Run it
./youtube_clipster.sh &
```

### Windows Installation

#### Option 1: Via GitHub

```batch
REM Clone the repository
git clone https://github.com/joruf/youtube-clipster.git

REM Change into the project directory
cd youtube-clipster

REM Run the batch file
youtube_clipster.bat
```

#### Option 2: Direct Download

1. Download `youtube_clipster.bat` from the [releases page](https://github.com/joruf/youtube-clipster/releases)
2. Download `youtube_clipster_bat.ps1` (companion PowerShell script for GUI)
3. Place both files in the same directory
4. Double-click `youtube_clipster.bat` to run

---

## Usage

1. **Start the program**:
   - **Linux**: Run `./youtube_clipster.sh &` in terminal
   - **Windows**: Double-click `youtube_clipster.bat`

2. **Copy a YouTube link** to your clipboard

3. **Select format** in the popup dialog:
   - **MP3** - Audio only
   - **MP4** - Video + Audio

4. **Wait for download** to complete

5. Files are saved to:
   - **Linux**: `~/Downloads`
   - **Windows**: `%USERPROFILE%\Downloads`

---

## Configuration

### Linux Configuration

Edit these variables in `youtube_clipster.sh`:

```bash
# Language (EN or DE)
LANG_CHOICE="EN"

# Download directory
DOWNLOAD_DIR="$HOME/Downloads"

# Show startup notification (0=hide, 1=show)
SHOW_STARTUP_DIALOG="1"

# Clipboard check interval in seconds
INTERVAL_TIME_SEC="2"
```

### Windows Configuration

Edit these variables in `youtube_clipster.bat`:

```batch
REM Language (EN or DE)
set "LANG_CHOICE=EN"

REM Download directory
set "DOWNLOAD_DIR=%USERPROFILE%\Downloads"

REM Show startup notification (0=hide, 1=show)
set "SHOW_STARTUP_DIALOG=1"

REM Enable Windows autostart (0=disable, 1=enable)
set "ENABLE_AUTOSTART=0"

REM Clipboard check interval in seconds
set "INTERVAL_TIME_SEC=2"
```

---

## Advanced Features

### Windows Autostart

To automatically start YouTube Clipster when Windows boots:

1. Open `youtube_clipster.bat` in a text editor
2. Change `set "ENABLE_AUTOSTART=0"` to `set "ENABLE_AUTOSTART=1"`
3. Run the script (may require administrator privileges)

The script will add itself to Windows Registry autostart.

### Linux Autostart

To start automatically on login:

#### For GNOME/Ubuntu:

1. Open **Startup Applications**
2. Click **Add**
3. Name: `YouTube Clipster`
4. Command: `/full/path/to/youtube_clipster.sh`
5. Click **Save**

#### For other desktop environments:

Add this line to your `~/.bashrc` or `~/.profile`:

```bash
/full/path/to/youtube_clipster.sh &
```

---

## Troubleshooting

### Linux

**Problem**: Script doesn't detect clipboard changes
- **Solution**: Ensure `xclip` (X11) or `wl-clipboard` (Wayland) is installed

**Problem**: Downloads fail with "verify you are not a robot"
- **Cause**: Too many consecutive downloads from same IP
- **Solution**: Wait a few minutes or renew your IP address

**Problem**: Missing dependencies
- **Solution**: The script auto-installs dependencies, but you can manually install:
  ```bash
  sudo apt update
  sudo apt install xclip wl-clipboard ffmpeg zenity
  pip install -U yt-dlp
  ```

### Windows

**Problem**: Script doesn't start
- **Solution**: Right-click `youtube_clipster.bat` and select "Run as Administrator"

**Problem**: PowerShell execution policy error
- **Solution**: The script handles this automatically, but you can manually run:
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  ```

**Problem**: Downloads fail
- **Solution**: Ensure you have an active internet connection and check the console output for errors

**Problem**: Multiple instances warning
- **Solution**: Close any existing YouTube Clipster instances before starting a new one

---

## How It Works

1. **Monitoring**: The script continuously monitors your system clipboard
2. **Detection**: When a YouTube URL is detected, processing begins
3. **Validation**: Checks if the link is valid and hasn't been processed before
4. **Format Selection**: Displays a dialog for format choice (MP3/MP4)
5. **Download**: Uses `yt-dlp` to download the content
6. **Conversion**: Uses `ffmpeg` to convert audio if MP3 is selected
7. **Completion**: Notifies you when done and opens the download folder

---

## Dependencies

### Linux
- **xclip** / **wl-clipboard**: Clipboard access
- **yt-dlp**: YouTube download engine
- **ffmpeg**: Audio/video processing
- **zenity**: GUI dialogs

### Windows
- **yt-dlp.exe**: YouTube download engine (auto-installed)
- **ffmpeg**: Audio/video processing (auto-installed)
- **PowerShell**: GUI dialogs (built-in)

---

## Important Notes

- ⚠️ **Rate Limiting**: YouTube may temporarily block downloads after many consecutive requests. This is an IP-based restriction. Wait a few minutes or change your IP address.
- 🔒 **Single Instance**: Only one instance can run at a time to prevent conflicts
- 📁 **File Names**: Downloaded files use the video title as the filename
- 🌐 **Network Required**: Active internet connection required for downloads

---

## License

**GPLv3** - The author's name (Joachim Ruf, Loresoft.de) must be credited upon publication and modification.

---

## Support

For issues, questions, or feature requests:
- Open an issue on [GitHub](https://github.com/joruf/youtube-clipster/issues)
- Check existing issues for solutions

---

## Changelog

### Version 1.0
- Initial release with Linux support
- Bash script for Debian-based distributions
- MP3 and MP4 download support

### Version 1.01 (Windows)
- Added Windows batch script version
- PowerShell GUI integration
- Auto-download dependencies
- Windows autostart support
- Progress tracking dialog

