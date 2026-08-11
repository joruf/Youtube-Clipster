# User guide

End-user guide for **Loresoft YouTube Clipster** — clipboard downloads and Streaming discovery on Linux, Windows, and macOS.

Screenshots below use anonymized sample data.

## What Clipster does

- Watches your clipboard for YouTube links and downloads them as MP3 or MP4.
- Keeps a download history with open / folder / remove actions.
- Offers a **Streaming** tab to find similar songs from your downloads, likes, and local media, and play them in-app with an optional stage visualizer.
- Runs from the system tray (when available) so it stays out of the way.

## Install

### Linux

```bash
git clone https://github.com/joruf/youtube-clipster.git
cd youtube-clipster
chmod +x install.sh run.py
./install.sh
```

The installer installs missing system packages (may ask for `sudo`) and creates a private virtual environment. You can also run `python3 run.py`.

### Windows

```bat
git clone https://github.com/joruf/youtube-clipster.git
cd youtube-clipster
run.bat
```

Or double-click `run.bat`. It starts without a console window and shows a small setup window naming
whatever is being installed at that moment; when everything is in place the program starts by itself.
The first start takes a few minutes because `yt-dlp` and `ffmpeg` are downloaded. Use `install.bat` if
you would rather watch the whole log in a console window.

Allow Defender/SmartScreen prompts for yt-dlp and ffmpeg on first run.

### Requirements (handled automatically)

Python 3.8+, tkinter, yt-dlp, ffmpeg, and a clipboard helper on Linux (`xclip` or `wl-clipboard`). Optional: tray packages (`pystray`, Pillow, …).

## On your phone

You can send links from an Android phone or an iPhone; the PC downloads them. Nothing to install - the
running program serves a small web page.

Open the view window and pick **Remote**. Switch on *Serve the phone interface*, choose *Every device on
my network*, and scan the QR code with the phone. The page says when a phone last reached the PC, so
you can see it working; it also shows the firewall command if one is needed.

![The Remote page](images/phone-page.png)

The phone has four tabs, and they can do what the desktop windows do - the phone interface *is* the
Android version, so it is kept level with the desktop rather than trailing it. **Downloads** for links
and the download list (sortable by name, length, size or date), **Streaming** as a remote control -
search, queue, transport, likes, volume, *Find similar*, *My downloads*, shuffle, repeat, the sleep
timer and the stage - plus **Settings** and **About**. Type into the search box, stop typing, and the
results appear; tap one and it plays and joins the queue. **Play on** decides whether the sound comes
out of the PC or out of the device itself - the latter is what you want when the phone is paired with a
speaker, and it is also when the stage is driven by the phone's own audio. Streaming needs its terms
accepted once on the PC before the phone may use it.

![Streaming from the phone](images/phone-streaming.png)

Without a window there is `python3 run.py --phone-setup`, which walks through the same steps in the
terminal. The full walkthrough, including the Android share sheet and the iPhone shortcut, is in
[README - Remote control](../README.md#remote-control-phone-tablet-another-pc).

![Phone interface](images/phone.png)

## First start (terms)

On first launch Clipster asks you to accept the **general terms of use**. You must tick the checkbox and choose Accept; Decline quits the program.

![Terms of use](images/terms.png)

When you first use **Streaming** (Find similar / playback), a separate Streaming terms dialog may appear. Acceptance is stored locally in your config (`terms_*` keys) with a version and timestamp.

The About page can open the terms again in read-only form.

![About](images/about.png)

## Clipboard downloads

1. Leave Clipster running (tray or view window).
2. Copy a YouTube link (`youtube.com/watch`, `youtu.be`, Shorts, …).
3. The small navigation window opens: confirm **MP3** or **MP4**, and pick an audio language if asked.
4. Optionally fill in **Section** to keep only a part of the video (see below).
5. Watch progress; use Cancel if needed.
6. When finished, open the file or its folder from the nav window.

You can also paste a URL into the **Downloads** page and press Download.

![Downloads list](images/downloads.png)

Tips:

- Several links queue up (they are not dropped while a download runs).
- The same URL + format is not downloaded again if the file still exists — you can open it or force a re-download.
- Files go to your Downloads folder unless you change **Download folder** in Settings.

### Only a section

The two small fields next to **Section** cut one piece out of the video instead of downloading all of it:

| From | To | Result |
|---|---|---|
| *(empty)* | *(empty)* | The whole video, as always |
| `0:45` | `2:10` | Everything between those two points |
| `1:30` | *(empty)* | From there to the end |
| *(empty)* | `0:30` | The first 30 seconds |

Times are written as `1:23` (minutes:seconds), `1:02:03` (hours:minutes:seconds) or as plain seconds (`90`). A field that is not a time — or an end before the start — is reported right in the window; nothing is downloaded until it makes sense.

The cut lands exactly on the second you named, not on the nearest keyframe, so the section is re-encoded and takes a little longer than the same amount of a plain download.

The file keeps the video title and says which piece it is: `Some song [0-45_2-10].mp3`. That way a section and the full video can sit next to each other, and downloading the same section twice is recognised just like a repeated full download. The section belongs to that one link — the next link starts with empty fields again.

### Working with the list

The **Downloads** page is a table you can arrange:

- **Sort** — click *Name*, *Length*, *Size* or *Date*. Clicking the same heading again reverses the order; an arrow marks the column in use. Length, size and date sort by their real value, not by the text, so `9 MB` stays below `10 MB`. The list starts with the newest download on top.
- **Column widths** — drag the divider on the right of *Length*, *Size* or *Date*. The name column takes whatever is left over, so pulling the others in gives long file names more space. The pointer turns into a double arrow over a divider.
- **Long names** — names too long for the column end in `…`; rest the pointer on one to see it in full.

Sorting and column widths apply to the window you are working in and start fresh on the next start.

## Streaming tab

Open the view window (tray click, or start with `--show-window`) and select **Streaming**.

![Streaming with Beat ring stage](images/streaming.png)

### Find similar songs

- **Find similar songs** — builds a queue from download history, liked tracks, and (if needed) media in Downloads / Music.
- **Search mode** — Related videos, YouTube search by title, Deezer similar artists, or ListenBrainz (free providers); sits next to Find similar songs.
- On startup (when Streaming terms are already accepted and the network is up), Clipster can start Find similar automatically in the background without opening the window.
- Title / search ending and the “only titles with that ending” filter live under **Settings** (Streaming section); Streaming uses those values when searching.

### Audio / Video

In **Now playing**:

- **Audio** — in-app audio playback with the stage visualizer (default).
- **Video** — embedded video when a suitable player backend (e.g. mpv) is available; otherwise Clipster may fall back to audio.

### Stage modes

The **Stage** combobox controls the visualizer while audio is playing. Order in the list:

| Label | Mode id | Notes |
|-------|---------|--------|
| Off | `off` | Blank stage |
| Text only | `text` | Minimal placeholder |
| Waveform | `waveform` | Oscilloscope-style line |
| Cover | `cover` | Thumbnail / title |
| Beat ring | `pulse` | Expanding ring driven by energy — **default for new installs** |
| Spectrum | `spectrum` | Frequency bars |
| Visualizer | `visualizer` | Generative mountain silhouette |

New installations default to **Beat ring** (`pulse`). Changing the Stage selection is saved to `discover_visualizer` in your config.

### Queue, likes, and downloads

- Click a queue row to play it; use transport controls for previous / next / pause.
- Per-row download sends that track through the normal download pipeline.
- Like / dislike (taste) votes are stored locally and help shape future recommendations.
- **Shuffle** plays the queue in random order — every song once before anything repeats, rather than
  the same three all evening.
- **Repeat** steps through off, the whole queue, and this one song. Repeating one song only applies
  when it ends by itself; pressing *next* always moves on.
- **Sleep timer** stops playback after 15 to 90 minutes.

### Your own downloads, and mobile data

**My downloads** fills the queue from your download folder instead of from YouTube. Those songs need
no connection at all — which is the point of the next setting.

Under **Settings → Playback**, *On mobile data* decides what Streaming does when the device is on a
mobile connection:

| Choice | What happens |
|--------|--------------|
| Stream online | Behaves as always. The default, so nothing changes for an existing install. |
| Downloaded songs only | The queue switches to your download folder, with a note saying why. |
| Ask every time | You are asked once per connection before anything is streamed. |

Only the device that is *on* the connection can tell what it is, so this takes effect on the phone;
a PC never reports a mobile connection and is never restricted by it. When you know the allowance is
gone, the *Always play downloaded songs only* switch overrides the detection everywhere.

### Sharing a song

Long-press a queue row or a download row (right-click on a desktop) and Clipster shows a QR code.
Someone else scans it — with Clipster's own **Scan** button, and the song drops straight into their
playlist. The code holds a plain YouTube link, so an ordinary camera app can read it too; it just
opens YouTube instead of Clipster.

Scanning needs a camera, so it appears in the Clipster app on Android. Opening the same page in a
browser over a network address hides the button: browsers only allow camera access on a local or
encrypted address, and no setting changes that.

Streaming requires accepting the Streaming terms once (see [First start](#first-start-terms)).

## Settings

![Settings](images/settings.png)

Useful options:

- Language (English / German)
- Default format (MP3 / MP4)
- Download folder and clipboard interval
- Tray / start minimized
- Parallel downloads
- Streaming search ending, mode, and result limits
- **YouTube cookies** — if downloads or Streaming fail with “Sign in to confirm you’re not a bot”, set **Cookies from browser** (Firefox, Chrome, Chromium, Brave, or Edge while logged into YouTube) or point to a `cookies.txt` export. Clipster never logs cookie contents.
- Update checks

Changes save to `config.json` (see paths below). A full key list is in the [README](../README.md#configuration) and [technical docs](TECHNICAL.md#configuration-keys-overview).

## Updates

If update checks are enabled, Clipster compares your install to the `main` branch on GitHub. From **About** you can check and apply updates when available.

- Git clone: fast-forward `git pull` when the tree is clean.
- Archive install: download ZIP overlay without touching your config or history.

Which version you have is read from git in a clone, and from a small marker file
(`clipster/BUILD_COMMIT`) in every other kind of install — including the one on your phone, which
carries no `.git` at all. The marker is written when the Android bundle is packed and again after
every update. An installation that predates this and has no marker says so and offers to fetch the
newest version rather than claiming to be current.

yt-dlp itself is updated on its own schedule (`update_check_hours`), or force with `python3 run.py --update`.

## Configuration paths

| Platform | Directory |
|----------|-----------|
| Linux / macOS | `~/.local/share/YoutubeClipster/` |
| Windows | `%LOCALAPPDATA%\YoutubeClipster\` |

Contains `config.json`, `history.json`, the log file, and the private venv. For a portable checkout, copy `config.example.json` to `config.json` next to `run.py`.

## Troubleshooting basics

**Clipboard not detected (Linux)**  
Install `xclip` (X11) or `wl-clipboard` (Wayland). Run with `--verbose` and look for `Clipboard backend: …`.

**No tkinter**  
Linux: install `python3-tk` (or distro equivalent). Windows: repair Python with tcl/tk enabled.

**Tray missing or no menu**  
The app still works — the view window stays available. Install tray deps / AppIndicator support, or use `--no-tray`. See the README troubleshooting section for package names.

**Already running**  
Only one instance is allowed. Quit from the tray or view window, or end the process.

**Downloads fail (“not a bot” / unavailable)**  
In **Settings**, set YouTube cookies from your browser or a cookies file, wait a bit, or update yt-dlp (`--update`). Check the failed row’s problem text on the Downloads page.

**Streaming won’t play**  
Accept Streaming terms; ensure network access; for video mode install mpv if prompted. Audio mode uses the built-in stage even without mpv. Bot blocks: same cookies settings as downloads.

**Broken environment**  
`python3 run.py --reinstall` rebuilds the venv.

Logs: `youtube-clipster.log` next to your config. Start with `python3 run.py --verbose` when reporting issues.

## More documentation

- [Technical documentation](TECHNICAL.md) — architecture, module map, config keys, testing
- [README](../README.md) — features, installer details, CLI flags
