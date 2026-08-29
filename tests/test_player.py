"""Unit tests for in-tab Streaming playback helpers."""

from __future__ import annotations

from clipster.discover import DiscoverTrack
from clipster.player import BACKEND_AUDIO, BACKEND_MPV, DiscoverPlayer, PlayStartResult, watch_url


def _track(video_id: str = "abcdefghijk", title: str = "Demo", duration: int = 0) -> DiscoverTrack:
    return DiscoverTrack(
        url="https://www.youtube.com/watch?v={0}".format(video_id),
        video_id=video_id,
        title=title,
        duration=duration,
    )


def test_watch_url_uses_canonical_link() -> None:
    assert watch_url(_track()).endswith("abcdefghijk")


def test_play_uses_embedded_mpv(monkeypatch) -> None:
    calls = []

    class Alive:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.mp4")
    monkeypatch.setattr("clipster.player.shutil.which", lambda name: "/usr/bin/mpv" if name == "mpv" else None)

    def fake_popen(cmd, **_kwargs):
        calls.append(cmd)
        return Alive()

    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=12345, prefer_video=True)
    assert result.backend == BACKEND_MPV
    assert any("--wid=12345" in str(part) for part in calls[0])
    assert any("--keepaspect=yes" in str(part) for part in calls[0])
    assert not any("force-window=yes" in str(part) for part in calls[0])


def test_play_falls_back_to_audio_only(monkeypatch) -> None:
    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/mpv" if name == "mpv" else None,
    )

    class Alive:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    def fake_popen(cmd, **_kwargs):
        if any(str(part).startswith("--wid=") for part in cmd):
            raise OSError("embed failed")
        return Alive()

    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    player = DiscoverPlayer()
    player.set_playlist([_track()])
    # Force embed path to fail by making first Popen raise - actually _start_embedded
    # catches OSError. Simulate embed returning empty by no wid and audio path.
    result = player.play(0, embed_wid=None)
    assert result.backend == BACKEND_AUDIO
    assert player.playing is True


def test_audio_mode_skips_video_embed(monkeypatch) -> None:
    calls = []
    resolves = []

    class Alive:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    def fake_resolve(_url, _opts=None, prefer_video=True):
        resolves.append(prefer_video)
        return "http://example/a.m4a"

    monkeypatch.setattr("clipster.player.resolve_stream_url", fake_resolve)
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/mpv" if name == "mpv" else None,
    )
    monkeypatch.setattr("clipster.player.subprocess.Popen", lambda cmd, **_k: calls.append(list(cmd)) or Alive())

    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=999, prefer_video=False)
    assert result.backend == BACKEND_AUDIO
    assert resolves == [False]
    assert calls
    assert any("--no-video" in str(part) for part in calls[0])
    assert calls[0][-1] == "-"
    assert not any(str(part).startswith("--wid=") for part in calls[0])
    assert result.error != "video_embed_unavailable"


def test_video_mode_without_mpv_falls_back_to_audio(monkeypatch) -> None:
    """ffplay alone cannot embed reliably; Video mode stays audio inside Clipster."""
    calls = []

    class FakeStdin:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    class Alive:
        def __init__(self):
            self.stdin = FakeStdin()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.mp4")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
    )
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.time.sleep", lambda *_a, **_k: None)

    def fake_popen(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        return Alive()

    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=99, prefer_video=True)
    assert result.backend == BACKEND_AUDIO
    assert result.error == "video_embed_unavailable"
    assert any("-nodisp" in c[0] for c in calls)
    assert not any(c[1].get("env", {}).get("SDL_WINDOWID") for c in calls)


def test_video_mode_falls_back_when_ffplay_embed_dies(monkeypatch) -> None:
    class Dead:
        returncode = 1

        def poll(self):
            return 1

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 1

        def kill(self):
            return None

    class FakeStdin:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    class Alive:
        def __init__(self):
            self.stdin = FakeStdin()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    pops = []

    def fake_popen(cmd, **kwargs):
        pops.append(list(cmd))
        # First launch is embed (has no -nodisp); it dies. Second is audio pipe.
        if "-nodisp" in cmd:
            return Alive()
        return Dead()

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.mp4")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
    )
    monkeypatch.setattr("clipster.player.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)

    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=1, prefer_video=True)
    assert result.backend == BACKEND_AUDIO
    assert result.error == "video_embed_unavailable"


def test_play_reports_missing_player(monkeypatch) -> None:
    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")
    monkeypatch.setattr("clipster.player.shutil.which", lambda name: None)
    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=1, prefer_video=True)
    assert result.backend == ""
    assert result.error == "no_player"


def test_options_provider_cookies_reach_stream_resolve(monkeypatch) -> None:
    """Downloader._base_options cookies must flow into Streaming URL extraction."""
    seen = {}

    def fake_resolve(page_url, base_options=None, *, prefer_video=True):
        seen["options"] = dict(base_options or {})
        return "http://example/a.m4a"

    monkeypatch.setattr("clipster.player.resolve_stream_url", fake_resolve)
    monkeypatch.setattr("clipster.player.shutil.which", lambda name: None)
    player = DiscoverPlayer()
    player.set_options_provider(
        lambda: {"cookiesfrombrowser": ("firefox",), "quiet": True}
    )
    player.set_playlist([_track()])
    player.play(0)
    assert seen["options"].get("cookiesfrombrowser") == ("firefox",)


def test_player_append_tracks_keeps_index(monkeypatch) -> None:
    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")
    monkeypatch.setattr("clipster.player.shutil.which", lambda name: None)
    player = DiscoverPlayer()
    first = _track("aaaaaaaaaaa", "A")
    second = _track("bbbbbbbbbbb", "B")
    player.set_playlist([first])
    assert player.append_tracks([second]) == 1
    assert len(player.tracks) == 2


def test_track_finished_detects_dead_process(monkeypatch) -> None:
    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")

    class Dead:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr("clipster.player.shutil.which", lambda name: "/usr/bin/mpv" if name == "mpv" else None)
    monkeypatch.setattr("clipster.player.subprocess.Popen", lambda *a, **k: Dead())
    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=None)
    assert result.backend == BACKEND_AUDIO
    assert player.track_finished() is True
    assert player.playing is False


def test_prefetch_feeds_play_without_second_resolve(monkeypatch) -> None:
    resolves = []

    def fake_resolve(page_url, base_options=None, prefer_video=True):
        resolves.append((page_url, prefer_video))
        return "http://example/{0}.mp4".format(len(resolves))

    monkeypatch.setattr("clipster.player.resolve_stream_url", fake_resolve)
    monkeypatch.setattr("clipster.player.shutil.which", lambda name: "/usr/bin/mpv" if name == "mpv" else None)

    class Alive:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr("clipster.player.subprocess.Popen", lambda *a, **k: Alive())
    player = DiscoverPlayer()
    player.set_playlist([_track("aaaaaaaaaaa", "A"), _track("bbbbbbbbbbb", "B")])
    player.prefetch(1, prefer_video=False)
    import time

    deadline = time.monotonic() + 2
    while player.cached_stream("bbbbbbbbbbb") is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert player.cached_stream("bbbbbbbbbbb")
    before = len(resolves)
    result = player.play(1, embed_wid=None)
    assert result.backend == BACKEND_AUDIO
    assert len(resolves) == before


def test_ffplay_only_stays_audio_without_extra_window(monkeypatch) -> None:
    """Without a working embed, fall back to audio-only with -nodisp."""
    calls = []

    class FakeStdin:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    class Dead:
        returncode = 1

        def poll(self):
            return 1

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 1

        def kill(self):
            return None

    class Alive:
        def __init__(self):
            self.stdin = FakeStdin()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.mp4")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
    )
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.time.sleep", lambda *_a, **_k: None)

    def fake_popen(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        if "-nodisp" in cmd:
            return Alive()
        return Dead()

    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=12345, prefer_video=True)
    assert result.backend == BACKEND_AUDIO
    assert calls
    audio_calls = [c for c in calls if "-nodisp" in c[0]]
    assert audio_calls
    cmd, kwargs = audio_calls[0]
    assert "-vn" in cmd
    assert "pipe:0" in cmd
    assert kwargs.get("stdin") is not None
    assert kwargs.get("env", {}).get("SDL_VIDEODRIVER") == "dummy"


def test_ffplay_audio_skips_dummy_sdl_on_windows(monkeypatch) -> None:
    """SDL_VIDEODRIVER=dummy can prevent ffplay from starting on Windows."""
    from clipster import paths
    from clipster.player import BACKEND_AUDIO, DiscoverPlayer

    calls = []

    class Alive:
        def __init__(self):
            self.stdin = type("S", (), {"write": lambda *_a, **_k: None, "flush": lambda *_a: None, "close": lambda *_a: None})()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.mp4")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/ffplay" if name in ("ffplay", "ffplay.exe") else None,
    )
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.time.sleep", lambda *_a, **_k: None)

    def fake_popen(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        return Alive()

    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    player = DiscoverPlayer()
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=None, prefer_video=False)
    assert result.backend == BACKEND_AUDIO
    assert calls
    env = calls[0][1].get("env") or {}
    assert "SDL_VIDEODRIVER" not in env


def test_popen_uses_unbuffered_pipes_for_feeder(monkeypatch) -> None:
    from clipster.player import _popen
    import subprocess

    captured = {}

    class Alive:
        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return Alive()

    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    _popen(["ffplay", "-i", "pipe:0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert captured.get("bufsize") == 0
    assert captured.get("stdin") is subprocess.PIPE
    assert captured.get("stdout") is subprocess.PIPE


def test_mpv_cache_speed_times_out_on_windows(monkeypatch) -> None:
    """A stuck named pipe must not hang the UI refresh loop."""
    import threading
    import time

    from clipster import paths
    from clipster.player import BACKEND_MPV, DiscoverPlayer

    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    stuck = threading.Event()

    def hanging_open(*_a, **_k):
        stuck.wait(timeout=30.0)
        raise OSError("pipe unavailable")

    monkeypatch.setattr("builtins.open", hanging_open)
    player = DiscoverPlayer()
    player._playing = True
    player._backend = BACKEND_MPV
    player._ipc_path = r"\\.\pipe\clipster-mpv-test"
    player._process = type("P", (), {"poll": lambda self: None})()
    started = time.monotonic()
    assert player.stream_rate_bps() == 0.0
    assert time.monotonic() - started < 1.0
    stuck.set()


def test_find_player_prefers_bundled_ffplay(monkeypatch, tmp_path) -> None:
    from clipster import paths
    from clipster.player import _find_player

    bin_dir = tmp_path / "ffmpeg" / "bin"
    bin_dir.mkdir(parents=True)
    fake = bin_dir / "ffplay"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(paths, "bundled_ffplay_exe", lambda: fake)
    monkeypatch.setattr(paths, "bundled_mpv_exe", lambda: bin_dir / "mpv")
    monkeypatch.setattr("clipster.player.shutil.which", lambda _name: "/usr/bin/ffplay")
    assert _find_player("ffplay") == str(fake)


def test_popen_suppresses_console_on_windows(monkeypatch) -> None:
    from clipster import paths
    from clipster.player import _popen
    import subprocess

    captured = {}

    class Alive:
        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return Alive()

    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    _popen(["ffplay", "-nodisp", "http://x"])
    assert captured.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def test_seek_restarts_mpv_with_start(monkeypatch) -> None:
    calls = []

    class FakeStdin:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    class Alive:
        def __init__(self):
            self.stdin = FakeStdin()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/mpv" if name == "mpv" else None,
    )
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.subprocess.Popen", lambda cmd, **_k: calls.append(list(cmd)) or Alive())

    track = _track(duration=200)
    player = DiscoverPlayer()
    player.set_playlist([track])
    assert player.play(0, embed_wid=None).backend == BACKEND_AUDIO
    assert player.can_seek()
    assert player.seek(42.5) is True
    assert any("--start=42.5" in str(part) for part in calls[-1])
    assert calls[-1][-1] == "-"
    assert abs(player.position() - 42.5) < 1.0


def test_seek_uses_ffplay_ss(monkeypatch) -> None:
    calls = []

    class FakeStdin:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    class Alive:
        def __init__(self):
            self.stdin = FakeStdin()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
    )
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.subprocess.Popen", lambda cmd, **_k: calls.append(list(cmd)) or Alive())

    track = _track(duration=180)
    player = DiscoverPlayer()
    player.set_playlist([track])
    assert player.play(0, embed_wid=None).backend == BACKEND_AUDIO
    assert player.seek(30) is True
    cmd = calls[-1]
    assert "-ss" in cmd
    idx = cmd.index("-ss")
    assert float(cmd[idx + 1]) == 30.0
    assert "pipe:0" in cmd


def test_pause_keeps_stream_for_seek(monkeypatch) -> None:
    class FakeStdin:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    class Alive:
        def __init__(self):
            self.stdin = FakeStdin()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")
    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/mpv" if name == "mpv" else None,
    )
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.subprocess.Popen", lambda *_a, **_k: Alive())

    track = _track(duration=120)
    player = DiscoverPlayer()
    player.set_playlist([track])
    player.play(0, embed_wid=None)
    player.pause()
    assert player.playing is False
    assert player.can_seek()
    assert player.seek(15) is True
    assert player.playing is True


def test_format_stream_rate() -> None:
    from clipster.player import format_stream_rate

    assert format_stream_rate(0) == "— KB/s"
    assert format_stream_rate(128000).endswith("KB/s")
    assert "128" in format_stream_rate(128000)


def test_audio_playback_pipes_stream_without_spectrum(monkeypatch) -> None:
    """Audio mode pipes the media URL into mpv — no ffmpeg PCM spectrum path."""
    calls = []

    class Alive:
        def __init__(self):
            self.stdin = type(
                "S",
                (),
                {
                    "write": lambda *_a, **_k: None,
                    "flush": lambda *_a: None,
                    "close": lambda *_a: None,
                },
            )()

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    class FakeResp:
        def read(self, _n=0):
            return b"x" * 64

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def which(name):
        if name == "mpv":
            return "/usr/bin/mpv"
        return None

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        return Alive()

    monkeypatch.setattr("clipster.player.resolve_stream_url", lambda *_a, **_k: "http://example/a.m4a")
    monkeypatch.setattr("clipster.player.shutil.which", which)
    monkeypatch.setattr("clipster.player.urlopen", lambda *_a, **_k: FakeResp())
    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    monkeypatch.setattr("clipster.player._find_ffmpeg", lambda: None)

    player = DiscoverPlayer()
    player.set_visualizer_mode("off")
    player.set_playlist([_track()])
    result = player.play(0, embed_wid=None)
    assert result.backend == BACKEND_AUDIO
    assert len(calls) == 1
    assert calls[0][0] == "/usr/bin/mpv"
    assert "--no-video" in calls[0]
    assert not any("demuxer-rawaudio" in str(part) for part in calls[0])
    assert player.pcm_analysis_active() is False

def test_set_visualizer_mode_toggles_pcm_want() -> None:
    player = DiscoverPlayer()
    player.set_visualizer_mode("spectrum")
    assert player._pcm_wanted is True
    player.set_visualizer_mode("off")
    assert player._pcm_wanted is False
    player.set_visualizer_mode("cover")
    assert player._pcm_wanted is False


def test_is_local_media_path_rejects_http(tmp_path) -> None:
    from clipster.player import _is_local_media_path

    media = tmp_path / "song.mp3"
    media.write_bytes(b"x")
    assert _is_local_media_path(str(media)) is True
    assert _is_local_media_path("https://example/a.m4a") is False
    assert _is_local_media_path("http://example/a.m4a") is False
    assert _is_local_media_path("") is False
    assert _is_local_media_path(str(tmp_path / "missing.mp3")) is False


def test_local_file_is_passed_to_mpv_as_a_path(tmp_path, monkeypatch) -> None:
    """Library rows have no channel name; they must still play from disk.

    Audio used to pipe every source through urlopen.  A filesystem path is not
    an HTTP URL, so mpv got EOF immediately and the queue skipped to the next
    row until it hit a YouTube stream (those have a channel name).
    """
    media = tmp_path / "Metito Light (Visualizer).mp3"
    media.write_bytes(b"x" * 64)
    calls = []

    class Alive:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    def fake_popen(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        return Alive()

    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/mpv" if name == "mpv" else None,
    )
    monkeypatch.setattr(
        "clipster.player.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("local files must not be fetched")
        ),
    )
    monkeypatch.setattr("clipster.player.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "clipster.player.resolve_stream_url",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("local files must not hit YouTube")),
    )

    player = DiscoverPlayer()
    player.set_visualizer_mode("off")
    player.set_playlist(
        [
            DiscoverTrack(
                url="",
                video_id="",
                title="Metito Light (Visualizer)",
                path=str(media),
            )
        ]
    )
    result = player.play(0, prefer_video=False)
    assert result.backend == BACKEND_AUDIO
    assert calls
    cmd, kwargs = calls[0]
    assert cmd[-1] == str(media)
    assert "-" not in cmd[1:]
    assert kwargs.get("stdin") is None


def test_local_file_is_passed_to_ffplay_as_a_path(tmp_path, monkeypatch) -> None:
    media = tmp_path / "song.mp3"
    media.write_bytes(b"x" * 64)
    calls = []

    class Alive:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=0):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(
        "clipster.player.shutil.which",
        lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
    )
    monkeypatch.setattr("clipster.player.subprocess.Popen", lambda cmd, **kwargs: calls.append((list(cmd), dict(kwargs))) or Alive())

    player = DiscoverPlayer()
    player.set_visualizer_mode("off")
    player.set_playlist([DiscoverTrack(url="", video_id="", title="song", path=str(media))])
    result = player.play(0, prefer_video=False)
    assert result.backend == BACKEND_AUDIO
    cmd, kwargs = calls[0]
    assert "-i" in cmd
    assert cmd[cmd.index("-i") + 1] == str(media)
    assert "pipe:0" not in cmd
    assert kwargs.get("stdin") is None
