"""The update service: looking, fetching, restarting.

Nothing here reaches GitHub - the API answer is faked - and nothing restarts:
the restart is only inspected, never executed.
"""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from clipster import paths, updater


def _api_answer(sha: str, message: str = "A new commit") -> io.BytesIO:
    """Return a fake GitHub commits response."""
    payload = {"sha": sha, "commit": {"message": message + "\n\nbody"}}
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class _Response(io.BytesIO):
    """Minimal stand-in for what urlopen returns."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


# ----------------------------------------------------------------------
# The repository
# ----------------------------------------------------------------------
def test_the_slug_is_taken_from_the_url() -> None:
    assert updater.repository_slug("https://github.com/joruf/youtube-clipster.git") == \
        "joruf/youtube-clipster"
    assert updater.repository_slug("https://github.com/joruf/youtube-clipster") == \
        "joruf/youtube-clipster"


def test_a_foreign_url_yields_no_slug() -> None:
    assert updater.repository_slug("https://example.com/whatever") == ""


def test_the_configured_repository_is_a_github_one() -> None:
    assert updater.repository_slug() == "joruf/youtube-clipster"


def test_a_directory_without_git_is_not_a_checkout(tmp_path: Path) -> None:
    assert updater.is_git_checkout(tmp_path) is False
    assert updater.local_commit(tmp_path) == ""


# ----------------------------------------------------------------------
# Checking
# ----------------------------------------------------------------------
def test_a_different_commit_means_an_update(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Response(_api_answer("b" * 40).getvalue()))
    monkeypatch.setattr(updater, "local_commit", lambda root=None: "a" * 40)
    info = updater.check(tmp_path)
    assert info.known and info.available
    assert info.local == "a" * 10 and info.remote == "b" * 10
    assert info.summary == "A new commit"


def test_the_same_commit_means_nothing_to_do(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Response(_api_answer("a" * 40).getvalue()))
    monkeypatch.setattr(updater, "local_commit", lambda root=None: "a" * 40)
    info = updater.check(tmp_path)
    assert info.known and not info.available


def test_an_unknown_local_commit_never_claims_an_update(monkeypatch, tmp_path: Path) -> None:
    """Without a checkout there is nothing to compare, so do not offer one."""
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Response(_api_answer("b" * 40).getvalue()))
    monkeypatch.setattr(updater, "local_commit", lambda root=None: "")
    info = updater.check(tmp_path)
    assert info.known and not info.available


def test_a_network_failure_is_reported_not_raised(monkeypatch, tmp_path: Path) -> None:
    def explode(*args, **kwargs):
        raise updater.urllib.error.URLError("no route to host")

    monkeypatch.setattr(updater.urllib.request, "urlopen", explode)
    info = updater.check(tmp_path)
    assert not info.known
    assert "no route" in info.error


def test_a_nonsense_answer_is_reported(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Response(b'{"nothing": true}'))
    info = updater.check(tmp_path)
    assert not info.known


# ----------------------------------------------------------------------
# The throttle
# ----------------------------------------------------------------------
def test_the_first_check_is_always_due() -> None:
    assert updater.due(24) is True


def test_zero_hours_always_checks() -> None:
    updater.mark_checked()
    assert updater.due(0) is True


def test_a_recent_check_is_not_repeated() -> None:
    updater.mark_checked()
    assert updater.due(24) is False


# ----------------------------------------------------------------------
# Applying through git
# ----------------------------------------------------------------------
@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    """Return a real, tiny git repository."""
    if not updater.shutil.which("git"):
        pytest.skip("git is not installed")
    root = tmp_path / "checkout"
    root.mkdir()
    for command in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@example.com"],
                    ["git", "config", "user.name", "Test"]):
        subprocess.run(command, cwd=root, check=True, stdout=subprocess.DEVNULL)
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=root, check=True,
                   stdout=subprocess.DEVNULL)
    return root


def test_a_checkout_is_recognised(checkout: Path) -> None:
    assert updater.is_git_checkout(checkout)
    assert len(updater.local_commit(checkout)) == 40


def test_local_changes_block_the_update(checkout: Path) -> None:
    """Someone's uncommitted work must never be thrown away."""
    (checkout / "file.txt").write_text("edited\n", encoding="utf-8")
    ok, message = updater.apply(checkout)
    assert not ok
    assert "local changes" in message.lower()


def test_without_a_remote_the_pull_fails_cleanly(checkout: Path) -> None:
    ok, message = updater.apply(checkout)
    assert not ok
    assert message


# ----------------------------------------------------------------------
# Applying through the archive
# ----------------------------------------------------------------------
def test_the_archive_replaces_files_but_keeps_user_data(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "install"
    (target / "clipster").mkdir(parents=True)
    (target / "clipster" / "app.py").write_text("old\n", encoding="utf-8")
    (target / "config.json").write_text("mine\n", encoding="utf-8")
    (target / "history.json").write_text("mine\n", encoding="utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("youtube-clipster-main/clipster/app.py", "new\n")
        bundle.writestr("youtube-clipster-main/README.md", "new readme\n")
        bundle.writestr("youtube-clipster-main/config.json", "SHOULD NOT LAND\n")
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Response(buffer.getvalue()))

    ok, message = updater.apply(target)
    assert ok, message
    assert (target / "clipster" / "app.py").read_text() == "new\n"
    assert (target / "README.md").read_text() == "new readme\n"
    assert (target / "config.json").read_text() == "mine\n", "user data must survive"
    assert (target / "history.json").read_text() == "mine\n"


def test_a_failed_download_is_reported(tmp_path: Path, monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise updater.urllib.error.URLError("offline")

    monkeypatch.setattr(updater.urllib.request, "urlopen", explode)
    ok, message = updater.apply(tmp_path)
    assert not ok
    assert "offline" in message


def test_a_broken_archive_is_reported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Response(b"not a zip at all"))
    ok, message = updater.apply(tmp_path)
    assert not ok


# ----------------------------------------------------------------------
# Restarting
# ----------------------------------------------------------------------
def test_the_restart_command_points_at_the_entry_point() -> None:
    command = updater.restart_command()
    assert len(command) == 2
    assert command[1].endswith("run.py")


def test_the_restart_replays_the_startup_arguments(monkeypatch) -> None:
    """Android runs --headless; coming back without it would find no display."""
    from clipster import cli

    monkeypatch.setattr(cli, "STARTUP_ARGUMENTS", ["--headless", "--skip-checks"])
    command = updater.restart_command()
    assert command[1].endswith("run.py")
    assert command[2:] == ["--headless", "--skip-checks"]


def test_the_restart_command_stays_bare_without_arguments(monkeypatch) -> None:
    from clipster import cli

    monkeypatch.setattr(cli, "STARTUP_ARGUMENTS", [])
    assert len(updater.restart_command()) == 2


def test_the_startup_arguments_cannot_be_changed_from_outside(monkeypatch) -> None:
    """restart_command must not hand out a list callers can mutate."""
    from clipster import cli

    monkeypatch.setattr(cli, "STARTUP_ARGUMENTS", ["--headless"])
    command = updater.restart_command()
    command.append("--nonsense")
    assert cli.STARTUP_ARGUMENTS == ["--headless"]


def test_the_restart_clears_the_relaunch_marker(monkeypatch) -> None:
    """Otherwise the fresh process would refuse to enter its environment."""
    import subprocess

    seen: dict = {}
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setenv("YOUTUBE_CLIPSTER_RELAUNCHED", "1")
    monkeypatch.setattr(updater.subprocess, "Popen",
                        lambda command, **kwargs: seen.update(kwargs) or None)
    updater.restart()
    assert "YOUTUBE_CLIPSTER_RELAUNCHED" not in seen["env"]
    assert seen.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
