# YouTube Clipster

**Loresoft YouTube Clipster** downloads YouTube videos or audio automatically as soon as you copy a
YouTube link to your clipboard.

Since **v2.0** the whole program is written in **Python** and runs from **one single code base** on
**Linux and Windows** (macOS works too). The former Bash edition (`linux/*.sh`) and Batch/PowerShell
edition (`windows/*.bat`) have been replaced by the `clipster/` package.

---

## Features

- **Runs in the background** – lives in the system tray and watches the clipboard
- **Two windows, each with one job**
  - a small **navigation window** that opens when you copy a link: pick format and audio track,
    watch the progress, see the result
  - a large **view window** with the download list, the settings and the about page
- **Download list** – name, length, size, date and status of every download, with per-row
  *Open* and *Folder* buttons, status filters and a problem description when something failed
- **Dark, modern interface** – one colour scheme (`clipster/theme.py`), identical on every platform
- **Format selection** – audio (MP3) or video (MP4), with a preselectable default
- **Audio track selection** – offered when a video has several languages
- **Declared dependencies** – everything the program needs is data in `clipster/dependencies.py`;
  the installer reads that table, works out what is missing, installs it and starts the program
- **Self-updating** – `yt-dlp` is kept up to date automatically
- **Multi-language** – English and German (`clipster/locales/*.json`)
- **Single instance** – a second start is refused with a clear message
- **Desktop integration** – optional desktop shortcut and login autostart

---

## Requirements

| | Linux / macOS | Windows |
|---|---|---|
| **Python** | 3.8 or newer | 3.8 or newer |
| **GUI** | `python3-tk` | included in the Python installer |
| **Clipboard** | `xclip` (X11) or `wl-clipboard` (Wayland) | built into Windows |
| **Download engine** | `yt-dlp` | `yt-dlp` |
| **Media processing** | `ffmpeg` | `ffmpeg` |
| **System tray** *(optional)* | `pystray`, `Pillow`, `python-xlib` | `pystray`, `Pillow` |

**You do not have to install any of this by hand.** The bootstrapper `youtube-clipster.py` checks
every component on each start and installs what is missing – see
[What the installer does](#what-the-installer-does).

Tested desktops: X11 and Wayland, Debian/Ubuntu/Mint/Pop!\_OS (`apt`), Fedora (`dnf`), Arch
(`pacman`), openSUSE (`zypper`), Alpine (`apk`), and Windows 10/11.

---

## Installation

### Linux

```bash
# 1. Clone the repository
git clone https://github.com/joruf/youtube-clipster.git
cd youtube-clipster

# 2. Make the starter executable (only needed once)
chmod +x install.sh youtube-clipster.py

# 3. Install everything that is missing and start the program
./install.sh
```

`install.sh` looks for a suitable Python 3, installs it through your package manager if it is
missing, and then hands over to `youtube-clipster.py`.

Installing system packages (`ffmpeg`, `python3-tk`, `xclip`, …) needs **root**, so you will be asked
for your `sudo` password **in the terminal**. Everything else is installed into your user profile
without root.

> Prefer to do it yourself? `python3 youtube-clipster.py` works exactly the same way, and
> `--no-auto-install` only reports what is missing instead of installing it.

### Windows

```bat
REM 1. Clone the repository (or download the ZIP from GitHub and unpack it)
git clone https://github.com/joruf/youtube-clipster.git
cd youtube-clipster

REM 2. Install everything that is missing and start the program
install.bat
```

Or simply **double-click `install.bat`** in Explorer.

If no Python is found, `install.bat` offers to install it via `winget`. Without `winget` it opens
<https://www.python.org/downloads/> – make sure **“Add python.exe to PATH”** is ticked during that
installation, then start `install.bat` again.

Windows Defender / SmartScreen may ask for confirmation on the first start because `yt-dlp` and
`ffmpeg` are downloaded. Allow the access.

---

## What the installer does

Everything the program needs is **declared as data** in
[`clipster/dependencies.py`](clipster/dependencies.py): the pip packages, the system tools, whether a
piece is required or optional, and what stops working without it. `clipster/installer.py` reads that
table, works out what is missing, installs it and then starts the program. Adding a requirement means
adding a row there – `requirements.txt` is generated from the same table:

```bash
python3 -c "from clipster import dependencies as d; print(d.requirements_text(), end='')" > requirements.txt
```

`youtube-clipster.py` runs the following steps on **every** start (they are skipped when everything
is already in place, so a warm start takes about a second):

1. **Python version** – aborts with a clear message on anything older than 3.8.
2. **tkinter** – Linux: installs the distribution package (`python3-tk`, `python3-tkinter`, `tk`, …).
   Windows: reports how to add it, since it ships with the official installer.
3. **Virtual environment** – creates a private venv so nothing is installed into your system Python:
   - Linux/macOS: `~/.local/share/YoutubeClipster/venv`
   - Windows: `%LOCALAPPDATA%\YoutubeClipster\venv`
   A broken or half-written environment is repaired or rebuilt automatically.
4. **yt-dlp** – installed into that venv and updated at most once every 24 hours
   (`update_check_hours`, or `--update` to force a check).
5. **FFmpeg** – Linux/macOS via the package manager; Windows downloads the official build and
   unpacks it to `%LOCALAPPDATA%\YoutubeClipster\ffmpeg`. An `ffmpeg` already in `PATH` is used as is.
6. **Clipboard helper** – Linux only: `xclip` or `wl-clipboard`, depending on X11 or Wayland.
   Tkinter is used as a fallback if neither can be installed.
7. **System tray** *(optional)* – installs `pystray`, `Pillow` and, on Linux, `python-xlib`.
   Never blocks the start; without them the view window is shown instead.
8. **JavaScript runtime** *(optional)* – `quickjs`/`node`/`deno` help a few yt-dlp extractors.
   Never blocks the start.

Afterwards the program restarts itself with the venv interpreter and begins monitoring the clipboard.

Check the setup without starting the program:

```bash
python3 youtube-clipster.py --check      # Linux/macOS
install.bat --check                      # Windows
```

---

## Usage

The program has **no main window**. It sits in the system tray and waits.

### 1. Copy a YouTube link

The small navigation window appears with the video title and its length. Choose the format and, on
multilingual videos, the audio track.

![Choose format](assets/screenshots/nav-choose.png)

### 2. Watch it download

The same window shows the progress, the speed and the remaining time. **Cancel** stops it.

![Downloading](assets/screenshots/nav-progress.png)

### 3. Done

The result stays on screen with buttons to open the file or its folder.

![Finished](assets/screenshots/nav-done.png)

Files are saved to your download folder:

- Linux/macOS: `~/Downloads` (the XDG folder is honoured)
- Windows: `%USERPROFILE%\Downloads`

### The view window

Open it from the tray icon, or let it open itself after every download
(`open_view_after_download`). It never gets in the way of a download.

![Download list](assets/screenshots/view-downloads.png)

- **Toolbar** – paste a link and press *Download* to start one by hand
- **Sidebar** – filter by *All*, *Ready*, *Failed* or *Canceled*, with live counts
- **Table** – name, length, size and date; *Open* plays the file, *Folder* reveals it in the file
  manager. Both are disabled once a file has been moved or deleted.
- **Failed rows** say what went wrong right under the name

Settings are edited in the same window and written straight to `config.json`:

![Settings](assets/screenshots/view-settings.png)

The about page lists the version, every path the program uses and the full dependency table:

![About](assets/screenshots/view-about.png)

### The system tray

| Action | Result |
|---|---|
| Left click / double click on the tray icon | opens the view window |
| Right click on the tray icon | menu: **Show window**, **Open download folder**, **Quit** |
| Closing the view window | hides it again – the program keeps running |
| **Quit** in the view window or in the tray menu | ends the program |

Hovering the icon shows what the program is currently doing.

Turn the tray off with `--no-tray` or `"use_tray": false`. Without a tray the view window is shown
at startup and closing it quits the program, so there is always a way out. See
[No tray icon appears](#no-tray-icon-appears).

---

## Configuration

The configuration is a JSON file that is created with defaults on the first start:

| Platform | Path |
|---|---|
| Linux/macOS | `~/.local/share/YoutubeClipster/config.json` |
| Windows | `%LOCALAPPDATA%\YoutubeClipster\config.json` |

For a **portable setup** copy `config.example.json` to `config.json` **next to
`youtube-clipster.py`** – that file then wins over the per-user one.

| Key | Default | Meaning |
|---|---|---|
| `language` | `"en"` | UI language, any file name in `clipster/locales` (`en`, `de`) |
| `download_dir` | `""` | Target folder; empty means the OS download folder |
| `interval_sec` | `2.0` | Clipboard polling interval in seconds |
| `show_startup_notification` | `true` | Short notification on start |
| `default_format` | `"mp3"` | Format preselected in the navigation window (`mp3` / `mp4`) |
| `open_view_after_download` | `false` | Open the view window when a download finished |
| `history_limit` | `100` | Maximum number of entries kept in the download list |
| `use_tray` | `true` | Place an icon in the system tray |
| `start_minimized` | `true` | Start in the tray without showing any window |
| `show_status_window` | `true` | Allow the view window to be shown at startup |
| `open_folder_after_download` | `true` | Open the target folder when a download finished |
| `file_manager` | `""` | Explicit file manager (e.g. `"nemo"`); empty uses the OS default |
| `clear_clipboard_after_download` | `true` | Empty the clipboard so the link is not processed twice |
| `ask_audio_language` | `true` | Ask for the audio track on multilingual videos |
| `no_playlist` | `true` | Download only the video, never the whole playlist |
| `restrict_filenames` | `false` | ASCII-only file names (old `--restrict-filenames` behaviour) |
| `output_template` | `"%(title)s.%(ext)s"` | yt-dlp output template |
| `user_agent` | `""` | Custom HTTP user agent |
| `ask_desktop_shortcut` | `true` | Ask once whether a desktop shortcut should be created |
| `autostart` | `false` | Start automatically at login |
| `update_check_hours` | `24` | Hours between two yt-dlp update checks (`0` = every start) |
| `log_level` | `"INFO"` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

The log file lives next to the configuration:
`~/.local/share/YoutubeClipster/youtube-clipster.log` (Windows: `%LOCALAPPDATA%\…`).

---

## Command line options

Both `install.sh` and `install.bat` forward every option to `youtube-clipster.py`.

```
--check               only check/install dependencies, do not start
--skip-checks         start without checking dependencies (fast start)
--update              force a yt-dlp update check
--reinstall           rebuild the virtual environment from scratch
--no-venv             use the current interpreter instead of a private venv
--no-auto-install     report missing components instead of installing them

--create-shortcut     create a desktop shortcut and exit
--autostart on|off    enable or disable the login autostart and exit

--config FILE         path to an alternative config.json
--lang CODE           UI language, e.g. de or en
--download-dir DIR    target directory for downloads
--no-window           never show the view window at startup
--no-tray             do not place an icon in the system tray
--show-window         start with the view window open
-v, --verbose         verbose (DEBUG) logging
--version             print the version
```

---

## Autostart and desktop shortcut

**Desktop shortcut** – offered once on the first start, or created explicitly at any time:

```bash
python3 youtube-clipster.py --create-shortcut     # Linux/macOS
install.bat --create-shortcut                     # Windows
```

- Linux: a freedesktop `.desktop` launcher on your desktop, marked executable and trusted
- Windows: a `.lnk` shortcut that starts without a console window

**Autostart at login**

```bash
python3 youtube-clipster.py --autostart on        # enable
python3 youtube-clipster.py --autostart off       # disable
```

- Linux: `~/.config/autostart/youtube-clipster.desktop`
- Windows: registry value `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\YouTubeClipster`

`"autostart": true` in `config.json` has the same effect and is applied on every start.

---

## Project structure

```
youtube-clipster/
├── youtube-clipster.py      # bootstrapper: dependency check, relaunch, start
├── install.sh               # Linux/macOS starter (finds or installs Python)
├── install.bat              # Windows starter (finds or installs Python)
├── requirements.txt         # generated from clipster/dependencies.py
├── config.example.json      # documented example configuration
├── tools/make_logo.py       # regenerates the logo (SVG + PNG + ICO)
└── clipster/
    ├── cli.py               # argument parsing, bootstrap, relaunch into the venv
    ├── dependencies.py      # THE dependency definition - what is needed and why
    ├── installer.py         # reads that table and installs what is missing
    ├── app.py               # clipboard monitor and download pipeline
    ├── theme.py             # the dark colour scheme and every ttk style
    ├── gui.py               # owns the hidden Tk root and both windows
    ├── navwindow.py         # small window: format, progress, result
    ├── viewwindow.py        # large window: list, settings, about
    ├── bridge.py            # marshals GUI calls onto the Tk thread (incl. Prompt)
    ├── downloader.py        # yt-dlp integration (metadata, download, progress)
    ├── history.py           # the persistent download list (history.json)
    ├── clipboard.py         # Win32 / wl-clipboard / xclip / xsel / pbpaste / Tk
    ├── singleinstance.py    # flock (POSIX) and named mutex (Windows)
    ├── shortcuts.py         # desktop shortcut, autostart, open file / reveal folder
    ├── config.py            # JSON configuration
    ├── i18n.py              # translations
    ├── paths.py             # platform paths
    ├── logging_setup.py     # console and file logging
    └── locales/             # en.json, de.json
```

Adding a language only means dropping a `clipster/locales/<code>.json` next to the existing files
and setting `"language": "<code>"`. Missing keys fall back to English.

---

## How it works

1. **Monitoring** – the clipboard is polled every `interval_sec` seconds.
2. **Detection** – a regular expression recognises `youtube.com/watch`, `youtu.be`, `/shorts/`,
   `/live/` and `/embed/` links.
3. **Metadata** – `yt-dlp` provides the title, the length and the available audio tracks.
4. **Question** – the navigation window asks for format and audio track in one step. The worker
   thread blocks on a `Prompt` while the Tk thread collects the answer.
5. **Download** – `yt-dlp` runs in that worker thread and reports exact progress values.
6. **Post-processing** – `ffmpeg` converts to MP3 or merges video and audio into MP4.
7. **Recording** – the outcome (finished, failed or canceled, with the reason) is appended to
   `history.json` and appears in the view window.
8. **Completion** – depending on the settings the clipboard is cleared, the download folder is
   opened and the view window comes up.

---

## Troubleshooting

### The clipboard is not detected (Linux)

Install the matching helper and restart the program:

```bash
sudo apt install xclip          # X11
sudo apt install wl-clipboard   # Wayland
```

Check which backend was chosen: `python3 youtube-clipster.py --verbose` prints
`Clipboard backend: …`.

### `ModuleNotFoundError: No module named 'tkinter'`

```bash
sudo apt install python3-tk       # Debian, Ubuntu, Mint
sudo dnf install python3-tkinter  # Fedora
sudo pacman -S tk                 # Arch
```

On Windows: re-run the Python installer, choose **Modify** and enable **tcl/tk and IDLE**.

### No tray icon appears

The program keeps working – it shows the view window instead. Check the reason first:

```bash
python3 youtube-clipster.py --verbose        # look for "System tray is unavailable"
```

- **Packages missing** – install them into the environment:
  `~/.local/share/YoutubeClipster/venv/bin/python -m pip install pystray Pillow python-xlib`
- **GNOME** – GNOME has no tray area of its own. Install the
  [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/),
  or run with `--no-tray`.
- **Wayland** – the X11 fallback backend needs XWayland. If the icon stays missing, install
  `gir1.2-ayatanaappindicator3-0.1` and `python3-gi`, or use `--no-tray`.
- **Force a backend** – `PYSTRAY_BACKEND=xorg python3 youtube-clipster.py`
  (also `appindicator`, `gtk`, `win32`).

### “Program is already running”

Only one instance is allowed. Quit the running one from its tray icon, or:

```bash
pkill -f youtube-clipster.py                     # Linux/macOS
taskkill /IM pythonw.exe /F                      # Windows
```

### Downloads fail with “confirm you are not a bot”

YouTube throttles by IP address after many consecutive downloads. Wait a few minutes or change your
IP. Also make sure yt-dlp is current: `python3 youtube-clipster.py --update`.

### The setup fails or the environment is broken

```bash
python3 youtube-clipster.py --reinstall
```

This deletes and rebuilds the virtual environment. Check the free disk space first – an interrupted
installation is almost always caused by a full disk.

### Nothing happens at all

Start with `--verbose` and read the log:

```bash
python3 youtube-clipster.py --verbose
cat ~/.local/share/YoutubeClipster/youtube-clipster.log
```

---

## Upgrading from v1.x (Bash / Batch)

- The scripts `linux/youtube-clipster.sh`, `linux/lib/*.sh`, `linux/config.cfg`,
  `windows/youtube-clipster.bat` and `windows/youtube-clipster.bat.ps1` are gone – the Python code
  base replaces all of them.
- `linux/config.cfg` and the `set "…"` block of the batch file became `config.json`. The new file is
  created with defaults on the first start; there is no automatic migration of the old values.
- Old desktop launchers and autostart entries point at the removed scripts. Recreate them with
  `--create-shortcut` and `--autostart on`.
- `linux/locales/*.cfg` became `clipster/locales/*.json`.
- The single status window of the first v2 builds was split into the small navigation window and
  the large view window, and the interface is now dark. Old `config.json` files keep working;
  the new keys (`default_format`, `open_view_after_download`, `history_limit`) take their
  defaults.

---

## Important notes

- **Rate limiting** – YouTube may temporarily block downloads after many consecutive requests. This
  is an IP-based restriction; wait a few minutes or change your IP.
- **Single instance** – only one instance may run at a time to prevent conflicts.
- **File names** – downloaded files are named after the video title (`output_template`).
- **Network** – an active internet connection is required.
- **Copyright** – only download content you are allowed to download.

---

## Logo

The mark is a red download triangle on a near-black tile, matching the interface colours. The vector
source and the raster files live in [`assets/icons/`](assets/icons/); regenerate them after editing
the SVG constants:

```bash
python3 tools/make_logo.py
```

That writes `youtube-clipster.svg` (source), `youtube-clipster.png` (512 px, used by Tk and the
tray), `youtube-clipster.ico` (multi-size, used by Windows) and a preview sheet.

![Logo at several sizes](assets/icons/youtube-clipster-preview.png)

---

## License

**GPLv3** – the author's name (Joachim Ruf, Loresoft.de) must be credited upon publication and
modification.

---

## Support

- Report issues on [GitHub](https://github.com/joruf/youtube-clipster/issues)
- Please attach the output of `python3 youtube-clipster.py --verbose`
