"""Checking GitHub for a newer version, fetching it and restarting.

The repository publishes neither releases nor tags, so the commit of the default
branch is what "newer" means here: the API reports the head commit, and it is
compared with the one this installation sits on.

Where that local commit comes from depends on how the program was installed:

* **checkout** - ``git rev-parse HEAD``.
* **anything else** - the marker file :data:`MARKER_NAME`, written when the
  installation was packed or last updated.  Android is exactly this case: the
  Termux bundle deliberately leaves ``.git`` behind, so without the marker there
  would be nothing to compare and the phone would report "newest version"
  forever.  An installation with no marker at all counts as *unknown*, which
  offers a reinstall rather than claiming to be current.

Two ways to apply an update:

* **git** - ``git pull --ff-only`` when the program runs from a checkout.  It
  refuses to run over local commits or a dirty tree, which is exactly the
  behaviour wanted: never silently throw away someone's work.
* **archive** - otherwise the branch ZIP is downloaded and unpacked over the
  installation.  Only files the archive contains are replaced; the download
  folder, the configuration and the download list live elsewhere and are never
  touched.

Nothing here runs automatically without being asked: :func:`check` only looks,
:func:`apply` only acts when the caller says so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from . import APP_URL, config as config_module, paths
from .logging_setup import get_logger
from .shortcuts import _no_window

log = get_logger(__name__)

#: Branch the update is taken from.
BRANCH = "main"
#: Seconds to wait for GitHub before giving up.
TIMEOUT = 20.0
#: State key holding the timestamp of the last check.
_STATE_KEY = "update_checked_at"

_USER_AGENT = "YouTubeClipster (+{0})".format(APP_URL)

#: Files and folders the archive update never overwrites.
_KEEP = frozenset({".git", "config.json", "history.json"})

#: Records which commit a non-git installation was built from, relative to the
#: installation directory.  Written by the Android bundler and by every archive
#: update; a checkout never has one because git already knows.
MARKER_NAME = Path("clipster") / "BUILD_COMMIT"


@dataclass
class UpdateInfo:
    """The outcome of one look at the repository."""

    #: ``True`` when the remote is ahead of this installation.
    available: bool = False
    #: Commit the installation sits on, short form; empty when unknown.
    local: str = ""
    #: Commit the branch points at, short form; empty when unreachable.
    remote: str = ""
    #: Subject line of the remote commit.
    summary: str = ""
    #: Why the check could not be made, empty on success.
    error: str = ""
    #: ``True`` when this installation cannot say which commit it holds.
    unknown: bool = False

    @property
    def known(self) -> bool:
        """Return ``True`` when the check produced a usable answer."""
        return not self.error and bool(self.remote)


def repository_slug(url: str = APP_URL) -> str:
    """Return ``owner/name`` for a GitHub repository URL.

    :param url: The repository URL.
    :return: The slug, or an empty string when the URL is not GitHub.
    """
    marker = "github.com/"
    if marker not in url:
        return ""
    slug = url.split(marker, 1)[1]
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug.strip("/")


def is_git_checkout(root: Optional[Path] = None) -> bool:
    """Return ``True`` when the installation is a usable git working tree.

    :param root: The installation directory; defaults to the project root.
    :return: Whether git can be used to update.
    """
    target = root or paths.PROJECT_ROOT
    return (target / ".git").exists() and shutil.which("git") is not None


def local_commit(root: Optional[Path] = None) -> str:
    """Return the commit the installation sits on.

    :param root: The installation directory; defaults to the project root.
    :return: The full SHA, or an empty string when it cannot be determined.
    """
    target = root or paths.PROJECT_ROOT
    if not is_git_checkout(target):
        return ""
    result = _run(["git", "rev-parse", "HEAD"], target)
    return result[1].strip() if result[0] == 0 else ""


def marker_path(root: Optional[Path] = None) -> Path:
    """Return where the build marker of an installation lives.

    :param root: The installation directory; defaults to the project root.
    :return: The path of the marker file, which need not exist.
    """
    return (root or paths.PROJECT_ROOT) / MARKER_NAME


def read_marker(root: Optional[Path] = None) -> str:
    """Return the commit recorded in the build marker.

    :param root: The installation directory; defaults to the project root.
    :return: The SHA, or an empty string when there is no readable marker.
    """
    try:
        text = marker_path(root).read_text(encoding="utf-8")
    except OSError:
        return ""
    commit = text.strip()
    # Anything that is not a plain hex SHA is treated as no marker at all: a
    # half-written file must not be compared against the remote commit.
    if not commit or len(commit) < 7 or any(c not in "0123456789abcdef" for c in commit.lower()):
        return ""
    return commit.lower()


def write_marker(commit: str, root: Optional[Path] = None) -> bool:
    """Record which commit this installation now holds.

    :param commit: The full SHA that was installed.
    :param root: The installation directory; defaults to the project root.
    :return: ``True`` when the marker was written.
    """
    if not commit:
        return False
    target = marker_path(root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(commit.strip() + "\n", encoding="utf-8")
    except OSError as exc:
        # A missing marker only costs the next check its comparison; it must
        # never turn a working update into a failed one.
        log.debug("The build marker could not be written: %s", exc)
        return False
    return True


def installed_commit(root: Optional[Path] = None) -> str:
    """Return the commit this installation holds, however it was installed.

    :param root: The installation directory; defaults to the project root.
    :return: The full SHA, or an empty string when it cannot be determined.
    """
    target = root or paths.PROJECT_ROOT
    if is_git_checkout(target):
        return local_commit(target)
    return read_marker(target)


def check(root: Optional[Path] = None) -> UpdateInfo:
    """Ask GitHub whether the branch is ahead of this installation.

    :param root: The installation directory; defaults to the project root.
    :return: What was found; never raises.
    """
    remote, summary, error = _fetch_head()
    if error:
        return UpdateInfo(error=error)

    local = installed_commit(root)
    mark_checked()
    return UpdateInfo(
        # An installation that cannot name its own commit is offered the update
        # rather than told it is current - the second is a lie it cannot back up.
        available=local != remote if local else True,
        unknown=not local,
        local=local[:10],
        remote=remote[:10],
        summary=summary,
    )


def _fetch_head() -> Tuple[str, str, str]:
    """Ask GitHub which commit the branch points at.

    :return: ``(sha, subject, error)``; the SHA is empty when the error is set.
    """
    slug = repository_slug()
    if not slug:
        return "", "", "{0} is not a GitHub repository".format(APP_URL)

    url = "https://api.github.com/repos/{0}/commits/{1}".format(slug, BRANCH)
    request = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("Update check failed: %s", exc)
        return "", "", str(exc)

    remote = str(payload.get("sha") or "")
    if not remote:
        return "", "", "the API returned no commit"
    summary = ""
    commit = payload.get("commit")
    if isinstance(commit, dict):
        summary = str(commit.get("message") or "").splitlines()[0] if commit.get("message") else ""
    return remote, summary, ""


def due(hours: int) -> bool:
    """Return ``True`` when the last check is older than ``hours``.

    :param hours: Minimum age before checking again; ``0`` always checks.
    :return: Whether a check should run now.
    """
    if hours <= 0:
        return True
    import time

    last = config_module.load_state().get(_STATE_KEY)
    if not isinstance(last, (int, float)):
        return True
    return (time.time() - float(last)) >= hours * 3600


def mark_checked() -> None:
    """Remember that a check just happened."""
    import time

    state = config_module.load_state()
    state[_STATE_KEY] = time.time()
    config_module.save_state(state)


# ----------------------------------------------------------------------
# Applying
# ----------------------------------------------------------------------
def apply(root: Optional[Path] = None) -> Tuple[bool, str]:
    """Fetch the new version into the installation.

    :param root: The installation directory; defaults to the project root.
    :return: ``(success, message)``; the message is meant for the user.
    """
    target = root or paths.PROJECT_ROOT
    if is_git_checkout(target):
        ok, message = _apply_git(target)
    else:
        ok, message = _apply_archive(target)
    if ok:
        # Drop compiled files so the process that starts next cannot keep
        # executing the version that was just replaced.
        purge_bytecode(target)
    return ok, message


def purge_bytecode(root: Optional[Path] = None) -> None:
    """Remove ``__pycache__`` folders from the installation.

    After an archive extract the ``.py`` files can be older than leftover
    ``.pyc`` files from the previous run.  Python then keeps the compiled
    copy, and the user still sees the version they just updated away from.

    The managed virtual environment is left alone: those files were not
    replaced by the update.

    :param root: The installation directory; defaults to the project root.
    :return: None
    """
    target = root or paths.PROJECT_ROOT
    skip = {".git", ".venv", "venv", "node_modules"}
    caches = [
        folder
        for folder in target.rglob("__pycache__")
        if folder.is_dir() and not skip.intersection(folder.parts)
    ]
    for folder in caches:
        shutil.rmtree(folder, ignore_errors=True)
    leftovers = [
        item
        for item in target.rglob("*.pyc")
        if item.is_file() and not skip.intersection(item.parts)
    ]
    for item in leftovers:
        try:
            item.unlink()
        except OSError:
            pass


def _apply_git(root: Path) -> Tuple[bool, str]:
    """Update a git checkout, refusing to touch local work.

    :param root: The working tree.
    :return: ``(success, message)``.
    """
    code, output = _run(["git", "status", "--porcelain"], root)
    if code != 0:
        return False, "git status failed: {0}".format(output.strip())
    if output.strip():
        return False, "There are local changes; commit or discard them first."

    code, output = _run(["git", "pull", "--ff-only"], root, timeout=180.0)
    if code != 0:
        return False, output.strip() or "git pull failed"
    log.info("Updated the checkout: %s", output.strip().splitlines()[-1:] or "")
    return True, output.strip()


def _apply_archive(root: Path) -> Tuple[bool, str]:
    """Download the branch archive and unpack it over the installation.

    The commit the archive was cut from is asked for first and written to the
    build marker afterwards, so the next check has something to compare against.
    The archive itself carries no such record - this is the only place it can
    come from.

    :param root: The installation directory.
    :return: ``(success, message)``.
    """
    slug = repository_slug()
    if not slug:
        return False, "no GitHub repository configured"
    head, _summary, head_error = _fetch_head()
    if head_error:
        log.debug("The new commit could not be named before the download: %s", head_error)
    url = "https://github.com/{0}/archive/refs/heads/{1}.zip".format(slug, BRANCH)

    with tempfile.TemporaryDirectory(prefix="clipster-update-") as work:
        archive = Path(work) / "update.zip"
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                archive.write_bytes(response.read())
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(work)
        except (urllib.error.URLError, OSError, zipfile.BadZipFile) as exc:
            return False, "Download failed: {0}".format(exc)

        unpacked = [item for item in Path(work).iterdir() if item.is_dir()]
        if len(unpacked) != 1:
            return False, "the archive has an unexpected layout"
        copied = _copy_tree(unpacked[0], root)

    write_marker(head, root)
    log.info("Updated %s files from the archive (%s).", copied, head[:10] or "commit unknown")
    return True, "{0} files updated".format(copied)


def _copy_tree(source: Path, target: Path) -> int:
    """Copy the archive contents over the installation.

    :param source: The unpacked archive root.
    :param target: The installation directory.
    :return: How many files were written.
    """
    written = 0
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if relative.parts and relative.parts[0] in _KEEP:
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        written += 1
    return written


# ----------------------------------------------------------------------
# Restarting
# ----------------------------------------------------------------------
def restart_command() -> List[str]:
    """Return the command that starts the program again.

    The arguments this process was started with are replayed: on Android the
    program runs ``--headless``, and a replacement started without that flag
    would look for a display that a phone does not have.

    ``--skip-checks`` is dropped so a new ``requirements.txt`` is installed
    before the new code runs.  On the desktop ``--show-window`` is added so
    the updated program comes back on screen instead of only in the tray.

    :return: Interpreter, entry point and the original arguments.
    """
    interpreter = paths.venv_python(gui=paths.IS_WINDOWS)
    if not interpreter.exists():
        interpreter = Path(sys.executable)
    command = [str(interpreter), str(paths.bootstrap_script())]
    from .cli import STARTUP_ARGUMENTS

    arguments = [item for item in STARTUP_ARGUMENTS if item != "--skip-checks"]
    if (
        "--headless" not in arguments
        and "--no-window" not in arguments
        and "--show-window" not in arguments
    ):
        arguments.append("--show-window")
    return command + arguments


def restart() -> None:
    """Replace the running program with a fresh one.

    On POSIX the process is replaced outright; Windows cannot do that, so a new
    one is spawned and this one is expected to exit right afterwards.

    :return: None
    """
    command = restart_command()
    log.info("Restarting: %s", " ".join(command))
    environment = dict(os.environ)
    # The child must not inherit "you already relaunched" from this process.
    environment.pop("YOUTUBE_CLIPSTER_RELAUNCHED", None)
    if paths.IS_WINDOWS:
        # Match installer/player: under pythonw.exe a console child would flash.
        subprocess.Popen(command, env=environment, close_fds=True, **_no_window())
        return
    try:
        os.execve(command[0], command, environment)
    except OSError as exc:
        log.error("Could not replace this process (%s); starting a new one.", exc)
        subprocess.Popen(command, env=environment, close_fds=True)
        os._exit(0)


def _run(command: List[str], cwd: Path, timeout: float = 60.0) -> Tuple[int, str]:
    """Run a command in ``cwd`` and capture its combined output.

    :param command: The argument vector.
    :param cwd: Working directory.
    :param timeout: Abort after this many seconds.
    :return: ``(exit code, output)``.
    """
    try:
        completed = subprocess.run(
            command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False, universal_newlines=True,
            **_no_window(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return completed.returncode, completed.stdout or ""
