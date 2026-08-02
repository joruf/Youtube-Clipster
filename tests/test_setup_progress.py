"""Tests for bootstrap progress reporting and setup splash helpers."""

from __future__ import annotations

from clipster.installer import bootstrap


def test_bootstrap_reports_progress(monkeypatch) -> None:
    messages = []

    monkeypatch.setattr("clipster.installer.check_python", lambda: __import__("clipster.installer", fromlist=["Step"]).Step(name="Python", ok=True))
    monkeypatch.setattr(
        "clipster.installer.check_tkinter",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="tkinter", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_venv",
        lambda recreate=False: __import__("clipster.installer", fromlist=["Step"]).Step(name="venv", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_ytdlp",
        lambda **_k: __import__("clipster.installer", fromlist=["Step"]).Step(name="yt-dlp", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_ffmpeg",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="FFmpeg", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_mpv",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="mpv", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_clipboard_tool",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="clipboard", ok=True),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_tray_menu",
        lambda interpreter=None, auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(
            name="tray-menu", ok=True
        ),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_tray_support",
        lambda interpreter=None, auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(
            name="tray", ok=True
        ),
    )
    monkeypatch.setattr(
        "clipster.installer.ensure_js_runtime",
        lambda auto_install=True: __import__("clipster.installer", fromlist=["Step"]).Step(name="js", ok=True),
    )
    monkeypatch.setattr("clipster.installer.paths.ensure_install_dir", lambda: None)
    monkeypatch.setattr("clipster.installer.paths.venv_python", lambda: __import__("pathlib").Path("/tmp/venv/bin/python"))

    report = bootstrap(on_progress=messages.append, use_venv=True)
    assert report.ok
    assert any("Python" in item for item in messages)
    assert any("yt-dlp" in item for item in messages)
    assert messages[-1].lower().startswith("dependency check finished")


def test_ensure_mpv_missing_does_not_fail_windows(monkeypatch) -> None:
    """Optional mpv must never block startup (Windows has no apt install)."""
    from clipster.installer import Step, ensure_mpv

    monkeypatch.setattr("clipster.installer.find_mpv", lambda: None)
    monkeypatch.setattr("clipster.installer.paths.IS_WINDOWS", True)
    monkeypatch.setattr("clipster.installer.paths.IS_LINUX", False)
    step = ensure_mpv(auto_install=True)
    assert isinstance(step, Step)
    assert step.ok is True
    assert "not installed" in step.detail.lower() or "mpv" in step.hint.lower()


def test_ensure_mpv_missing_does_not_fail_linux_without_install(monkeypatch) -> None:
    from clipster.installer import ensure_mpv

    monkeypatch.setattr("clipster.installer.find_mpv", lambda: None)
    monkeypatch.setattr("clipster.installer.paths.IS_WINDOWS", False)
    monkeypatch.setattr("clipster.installer.paths.IS_LINUX", True)
    step = ensure_mpv(auto_install=False)
    assert step.ok is True


def test_run_command_suppresses_console_on_windows(monkeypatch) -> None:
    """pip/venv children must not flash a console under pythonw.exe."""
    import subprocess

    from clipster import paths
    from clipster.installer import run_command

    captured = {}

    class FakeStdout:
        def __iter__(self):
            return iter(())

    class FakeProc:
        stdout = FakeStdout()
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr("clipster.installer.subprocess.Popen", fake_popen)
    # Also patch the helper so the flag is present even if the host OS is Linux.
    monkeypatch.setattr(
        "clipster.installer._no_window",
        lambda: {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)},
    )
    result = run_command(["echo", "ok"], echo=False, timeout=5.0)
    assert result.ok
    assert captured.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
