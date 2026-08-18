"""Command line interface, bootstrap logic and process relaunch.

``run.py`` calls :func:`bootstrap_main` with the *system* Python:
dependencies are checked and installed, then the program restarts itself inside
the managed virtual environment where ``yt-dlp`` lives.  ``python -m clipster``
calls :func:`main` directly and expects a ready environment.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, List, Optional, Sequence

from . import APP_TITLE, APP_VERSION, i18n, installer, paths, shortcuts
from . import logging_setup
from .config import Config

#: Set in the child process so a broken relaunch cannot loop forever.
_RELAUNCH_ENV = "YOUTUBE_CLIPSTER_RELAUNCHED"

#: What this process was started with, so an update can start the replacement
#: the same way.  Android runs ``--headless``; restarting without it would try
#: to open a window on a phone that has no display and the program would be
#: gone until the launcher is tapped again.
STARTUP_ARGUMENTS: List[str] = []

log = logging_setup.get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser shared by both entry points."""
    parser = argparse.ArgumentParser(
        prog="run.py",
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
    setup.add_argument("-y", "--yes", action="store_true",
                       help="install missing system packages without asking first")

    integration = parser.add_argument_group("desktop integration")
    integration.add_argument("--phone-setup", action="store_true",
                             help="guided setup that connects your phone, then exit")
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
    runtime.add_argument("--no-window", action="store_true",
                         help="start in the tray without opening the view window")
    runtime.add_argument("--no-tray", action="store_true", help="do not place an icon in the system tray")
    runtime.add_argument("--show-window", action="store_true",
                         help="open the view window at startup")
    runtime.add_argument("--headless", action="store_true",
                         help="run without windows or tray, driven by the remote interface")
    runtime.add_argument("--accept-terms", action="store_true",
                         help="confirm the terms of use (only needed with --headless)")
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
    splash = None
    # Checked before anything can create the file: on the very first start the
    # window is opened instead of going straight to the tray.
    first_start = not (Path(args.config).expanduser() if args.config else paths.config_file()).is_file()

    if not args.skip_checks:
        print(_banner(), file=sys.stderr)
        messages = i18n.load(_startup_language(args))
        # Visible feedback while packages install — otherwise a double-click on
        # run.py (or run.bat, which starts without a console) looks frozen.
        try:
            from .setup_ui import open_setup_splash

            # Nothing to show a splash on, and nothing to show it to.
            splash = None if args.headless else open_setup_splash(messages)
        except Exception as exc:  # pragma: no cover - headless / missing Tk
            log.debug("Setup splash could not be opened: %s", exc)
            splash = None

        def on_progress(message: str) -> None:
            _print_progress(message)
            if splash is not None:
                splash.set_status(message)

        try:
            on_progress(messages.get("setup_checking", "Checking dependencies..."))
            report = installer.bootstrap(
                auto_install=not args.no_auto_install,
                use_venv=use_venv,
                force_update=args.update,
                recreate_venv=args.reinstall,
                update_check_hours=_configured_update_hours(args.config),
                on_progress=on_progress,
                need_gui=not args.headless,
                # Ask before touching the system with the package manager. With no
                # terminal to answer on this installs as before, so a double-click
                # start and the setup scripts never stall on an unseen question.
                ask=not args.yes,
            )
            if report.ok and not (args.check or args.create_shortcut or args.autostart):
                on_progress(messages.get("setup_starting", "Starting YouTube Clipster..."))
        finally:
            if splash is not None:
                splash.close()
                splash = None

        if not report.ok:
            _report_failures(report, messages)
            return 1
        log.info("All components are ready.")

    if args.create_shortcut:
        return _create_shortcut()
    if args.autostart:
        return 0 if shortcuts.set_autostart(args.autostart == "on") else 1
    if args.check:
        return 0

    arguments = _first_start_arguments(arguments, args, first_start)

    if use_venv and not paths.running_in_managed_venv():
        return _relaunch_in_venv(arguments)

    return main(arguments)


def _first_start_arguments(arguments: List[str], args: argparse.Namespace, first_start: bool) -> List[str]:
    """Open the window on the very first start.

    ``start_minimized`` defaults to true, so a fresh installation would finish
    by putting an icon into the tray and nothing else - after minutes of
    downloading that looks like a program that failed to start.

    :param arguments: The current argument list.
    :param args: The parsed arguments.
    :param first_start: Whether no configuration file existed yet.
    :return: The argument list to hand on.
    """
    if not first_start or args.no_window or args.show_window or args.check or args.headless:
        return arguments
    log.info("First start - opening the window so the program is visible.")
    return arguments + ["--show-window"]


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


def _startup_language(args: argparse.Namespace) -> str:
    """Determine the interface language before the configuration is loaded.

    The dependency phase runs before :func:`main` reads the configuration, but
    the setup window already needs translated strings.

    :param args: The parsed command line arguments.
    :return: A language code, falling back to ``en``.
    """
    if args.lang:
        return str(args.lang)
    target = Path(args.config).expanduser() if args.config else paths.config_file()
    if target.is_file():
        try:
            return Config.load(target).language or "en"
        except Exception:  # pragma: no cover - unreadable config must not stop setup
            log.debug("Language could not be read from %s", target)
    return "en"


def _print_progress(message: str) -> None:
    """Echo one setup step to the console, whatever its code page can encode.

    The translated texts contain typographic characters (dashes, ellipses) that
    a legacy Windows code page cannot represent. Reporting progress must never
    be the thing that breaks the installation.

    :param message: The status line from the installer.
    :return: None
    """
    try:
        print("  {0}".format(message), file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        plain = message.encode("ascii", "replace").decode("ascii")
        print("  {0}".format(plain), file=sys.stderr, flush=True)
    except OSError:  # pragma: no cover - closed or full stream
        log.debug("Progress line could not be printed")


def _console_is_visible() -> bool:
    """Return ``True`` when the user can actually read what is printed.

    ``run.bat`` starts ``pythonw.exe`` (no console, ``sys.stderr`` is ``None``)
    and the Linux desktop launcher redirects stderr into the session log. In
    both cases a printed error never reaches anybody, so it has to be shown in
    a dialog instead.

    :return: ``True`` only for a real terminal.
    """
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - closed stream
        return False


def summarize_failures(report: installer.InstallReport, max_lines: int = 8) -> str:
    """Turn the failed steps of a report into a readable block of text.

    :param report: The finished install report.
    :param max_lines: Maximum number of components listed before summarising.
    :return: One text block, without a trailing newline.
    """
    # Plain ASCII: this text is printed to a console whose code page may not be
    # able to encode a bullet, and a UnicodeEncodeError here would replace the
    # error message with a traceback.
    failures = list(report.failures)
    lines = []
    for step in failures[:max_lines]:
        lines.append("  * {0}: {1}".format(step.name, step.detail or "missing"))
        if step.hint:
            lines.append("    -> {0}".format(step.hint))
    if len(failures) > max_lines:
        lines.append("  ... and {0} more".format(len(failures) - max_lines))
    return "\n".join(lines)


def _report_failures(report: installer.InstallReport, messages: Optional[i18n.Messages] = None) -> None:
    """Report everything that could not be installed, visibly.

    :param report: The finished install report.
    :param messages: Optional catalogue for the dialog shown without a console.
    :return: None
    """
    summary = summarize_failures(report)
    print("", file=sys.stderr)
    print("Setup incomplete - the following components are missing:", file=sys.stderr)
    print(summary, file=sys.stderr)
    print("", file=sys.stderr)

    if _console_is_visible():
        return
    intro = "Setup incomplete - the following components are missing:"
    title = APP_TITLE
    if messages is not None:
        intro = messages.get("setup_failed_intro", intro)
        title = messages.get("setup_failed_title", title)
    try:
        from .setup_ui import show_setup_failure

        show_setup_failure(title, "{0}\n\n{1}".format(intro, summary))
    except Exception as exc:  # pragma: no cover - headless session
        log.debug("Setup failure dialog could not be shown: %s", exc)


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


def _started_without_console() -> bool:
    """Return ``True`` when this process runs under ``pythonw.exe``.

    :return: ``False`` on every platform other than Windows.
    """
    if not paths.IS_WINDOWS:
        return False
    # PureWindowsPath rather than Path: it splits on backslashes on every
    # platform, which keeps this check verifiable outside Windows.
    return PureWindowsPath(sys.executable).name.lower().startswith("pythonw")


def _relaunch_in_venv(arguments: List[str]) -> int:
    """Restart the bootstrap script with the virtual environment interpreter.

    :param arguments: The original command line arguments.
    :return: The exit code of the relaunched process.
    """
    # Started from the desktop shortcut the parent is pythonw.exe, which has no
    # console. Relaunching into python.exe would pop one open for the whole
    # session, so the console-less interpreter is matched.
    interpreter = paths.venv_python(gui=_started_without_console())
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
    STARTUP_ARGUMENTS[:] = arguments

    config = Config.load(Path(args.config).expanduser() if args.config else None)
    if args.lang:
        config.language = args.lang
    if args.download_dir:
        config.download_dir = args.download_dir
    if args.no_window:
        config.start_minimized = True
    if args.no_tray:
        config.use_tray = False
    if args.show_window:
        config.start_minimized = False

    logging_setup.configure(verbose=args.verbose, level=config.log_level)
    messages = i18n.load(config.language)

    if args.phone_setup:
        # Before the instance lock: the wizard may well be run while the program
        # is already sitting in the tray.
        from . import phonesetup

        return phonesetup.run(config)

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

    app = None
    try:
        from .app import ClipsterApp
    except ImportError as exc:
        log.error("The application could not be initialised: %s", exc)
        if not args.headless:
            log.error("On Linux install the tkinter package, e.g. 'sudo apt install python3-tk'.")
        lock.release()
        return 1

    try:
        app = ClipsterApp(config, messages, headless=args.headless,
                          accept_terms=args.accept_terms)
        return app.run()
    finally:
        release_lock_and_relaunch(lock, app)


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


def release_lock_and_relaunch(lock: Any, app: Any) -> None:
    """Drop the instance lock, then start the version that was just installed.

    The replacement must not start while this process still holds the lock.
    Windows would then refuse the second instance, and the user would have to
    launch Clipster by hand even though they already asked for a restart.

    :param lock: The :class:`~clipster.singleinstance.SingleInstance` of this run.
    :param app: The application, or ``None`` when it never started.
    :return: None
    """
    lock.release()
    if app is None or not getattr(app, "_restart_after_update", False):
        return
    from . import updater

    updater.restart()


def print_version() -> None:
    """Print the application version (used by the wrapper scripts)."""
    print("{0} ({1})".format(APP_TITLE, APP_VERSION))
