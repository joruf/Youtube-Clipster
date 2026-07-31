"""Command line interface, bootstrap logic and process relaunch.

``youtube-clipster.py`` calls :func:`bootstrap_main` with the *system* Python:
dependencies are checked and installed, then the program restarts itself inside
the managed virtual environment where ``yt-dlp`` lives.  ``python -m clipster``
calls :func:`main` directly and expects a ready environment.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import APP_TITLE, APP_VERSION, i18n, installer, paths, shortcuts
from . import logging_setup
from .config import Config

#: Set in the child process so a broken relaunch cannot loop forever.
_RELAUNCH_ENV = "YOUTUBE_CLIPSTER_RELAUNCHED"

log = logging_setup.get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser shared by both entry points."""
    parser = argparse.ArgumentParser(
        prog="youtube-clipster",
        description="Loresoft YouTube Clipster - download YouTube videos by copying a link.",
    )
    parser.add_argument("--version", action="version", version=APP_TITLE)
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose (DEBUG) logging")

    setup = parser.add_argument_group("setup")
    setup.add_argument("--check", action="store_true", help="only check/install dependencies, do not start")
    setup.add_argument("--skip-checks", action="store_true", help="start without checking dependencies")
    setup.add_argument("--update", action="store_true", help="force a yt-dlp update check")
    setup.add_argument("--reinstall", action="store_true", help="rebuild the virtual environment from scratch")
    setup.add_argument("--no-venv", action="store_true", help="use the current interpreter instead of a private venv")
    setup.add_argument("--no-auto-install", action="store_true", help="report missing components instead of installing")

    integration = parser.add_argument_group("desktop integration")
    integration.add_argument("--create-shortcut", action="store_true", help="create a desktop shortcut and exit")
    integration.add_argument(
        "--autostart",
        choices=("on", "off"),
        help="enable or disable the login autostart and exit",
    )

    runtime = parser.add_argument_group("runtime")
    runtime.add_argument("--config", metavar="FILE", help="path to an alternative config.json")
    runtime.add_argument("--lang", metavar="CODE", help="UI language, e.g. de or en")
    runtime.add_argument("--download-dir", metavar="DIR", help="target directory for downloads")
    runtime.add_argument("--no-window", action="store_true", help="hide the small status window")
    runtime.add_argument("--no-tray", action="store_true", help="do not place an icon in the system tray")
    runtime.add_argument("--show-window", action="store_true", help="start with the status window open")
    return parser


# ----------------------------------------------------------------------
# Bootstrap entry point (system Python)
# ----------------------------------------------------------------------
def bootstrap_main(argv: Optional[Sequence[str]] = None) -> int:
    """Check dependencies, then start (or relaunch into) the application.

    :param argv: Argument list; defaults to ``sys.argv[1:]``.
    :return: The process exit code.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    logging_setup.configure(verbose=args.verbose)

    use_venv = not args.no_venv

    if not args.skip_checks:
        print(_banner(), file=sys.stderr)
        report = installer.bootstrap(
            auto_install=not args.no_auto_install,
            use_venv=use_venv,
            force_update=args.update,
            recreate_venv=args.reinstall,
            update_check_hours=_configured_update_hours(args.config),
        )
        if not report.ok:
            _report_failures(report)
            return 1
        log.info("All components are ready.")

    if args.create_shortcut:
        return _create_shortcut()
    if args.autostart:
        return 0 if shortcuts.set_autostart(args.autostart == "on") else 1
    if args.check:
        return 0

    if use_venv and not paths.running_in_managed_venv():
        return _relaunch_in_venv(arguments)

    return main(arguments)


def _banner() -> str:
    """Return the startup banner printed before the dependency check."""
    line = "=" * 60
    return "{0}\n  {1}\n  Author: Joachim Ruf, Loresoft.de - License: GPLv3\n{0}".format(line, APP_TITLE)


def _configured_update_hours(config_path: Optional[str]) -> int:
    """Read ``update_check_hours`` without forcing the config to be created.

    :param config_path: Optional explicit config file from the command line.
    :return: The configured number of hours (default 24).
    """
    target = Path(config_path).expanduser() if config_path else paths.config_file()
    if not target.is_file():
        return 24
    return Config.load(target).update_check_hours


def _report_failures(report: installer.InstallReport) -> None:
    """Print a readable summary of everything that could not be installed.

    :param report: The finished install report.
    :return: None
    """
    print("", file=sys.stderr)
    print("Setup incomplete - the following components are missing:", file=sys.stderr)
    for step in report.failures:
        print("  * {0}: {1}".format(step.name, step.detail or "missing"), file=sys.stderr)
        if step.hint:
            print("    -> {0}".format(step.hint), file=sys.stderr)
    print("", file=sys.stderr)


def _create_shortcut() -> int:
    """Create the desktop shortcut and report the result.

    :return: The process exit code.
    """
    try:
        target = shortcuts.create_desktop_shortcut()
    except OSError as exc:
        log.error("Desktop shortcut could not be created: %s", exc)
        return 1
    log.info("Desktop shortcut created: %s", target)
    return 0


def _relaunch_in_venv(arguments: List[str]) -> int:
    """Restart the bootstrap script with the virtual environment interpreter.

    :param arguments: The original command line arguments.
    :return: The exit code of the relaunched process.
    """
    interpreter = paths.venv_python()
    script = paths.bootstrap_script()

    if os.environ.get(_RELAUNCH_ENV):
        log.error("Relaunch into %s did not take effect - starting with the current interpreter.", interpreter)
        return main(arguments)
    if not interpreter.exists() or not script.is_file():
        log.warning("Virtual environment interpreter not found - starting with the current interpreter.")
        return main(arguments)

    forwarded = [item for item in arguments if item not in ("--check", "--update", "--reinstall")]
    if "--skip-checks" not in forwarded:
        forwarded.append("--skip-checks")

    environment = dict(os.environ)
    environment[_RELAUNCH_ENV] = "1"
    command = [str(interpreter), str(script)] + forwarded
    log.debug("Relaunching: %s", " ".join(command))

    if paths.IS_WINDOWS:
        completed = subprocess.run(command, env=environment, check=False)
        return completed.returncode
    os.execve(str(interpreter), command, environment)
    return 0  # pragma: no cover - execve does not return


# ----------------------------------------------------------------------
# Application entry point (virtual environment Python)
# ----------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    """Load the configuration, take the instance lock and run the application.

    :param argv: Argument list; defaults to ``sys.argv[1:]``.
    :return: The process exit code.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)

    config = Config.load(Path(args.config).expanduser() if args.config else None)
    if args.lang:
        config.language = args.lang
    if args.download_dir:
        config.download_dir = args.download_dir
    if args.no_window:
        config.show_status_window = False
    if args.no_tray:
        config.use_tray = False
    if args.show_window:
        config.start_minimized = False

    logging_setup.configure(verbose=args.verbose, level=config.log_level)
    messages = i18n.load(config.language)

    from .singleinstance import AlreadyRunning, SingleInstance

    lock = SingleInstance()
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        text = (
            messages.format("only_one_instance_pid", pid=exc.pid)
            if exc.pid
            else messages["only_one_instance"]
        )
        log.error("%s", text)
        _show_startup_error(messages["window_title"], text)
        return 1

    try:
        from .app import ClipsterApp
    except ImportError as exc:
        log.error("The GUI could not be initialised: %s", exc)
        log.error("On Linux install the tkinter package, e.g. 'sudo apt install python3-tk'.")
        lock.release()
        return 1

    try:
        return ClipsterApp(config, messages).run()
    finally:
        lock.release()


def _show_startup_error(title: str, text: str) -> None:
    """Show an error dialog before the main window exists.

    :param title: The window title.
    :param text: The error message.
    :return: None
    """
    try:
        from .gui import show_startup_error
    except ImportError:
        return
    show_startup_error(title, text)


def print_version() -> None:
    """Print the application version (used by the wrapper scripts)."""
    print("{0} ({1})".format(APP_TITLE, APP_VERSION))
