"""Console and file logging in the format used by the original shell edition."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from . import paths

LOGGER_NAME = "clipster"

_LEVEL_LABELS = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARN",
    "ERROR": "ERROR",
    "CRITICAL": "FATAL",
}

_LEVEL_COLORS = {
    "DEBUG": "\033[34m",
    "INFO": "\033[32m",
    "WARN": "\033[33m",
    "ERROR": "\033[31m",
    "FATAL": "\033[35m",
}

_RESET = "\033[0m"


def _enable_windows_ansi() -> bool:
    """Switch the Windows console into VT mode so ANSI colours work."""
    if not paths.IS_WINDOWS:
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # -11 == STD_OUTPUT_HANDLE, -12 == STD_ERROR_HANDLE
        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            if handle in (0, -1):
                return False
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            # 0x0004 == ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:  # pragma: no cover - console detection is best effort
        return False


def _supports_color(stream: object) -> bool:
    """Return ``True`` when ANSI escape sequences may be written to ``stream``."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(stream, "isatty") or not stream.isatty():  # type: ignore[union-attr]
        return False
    return _enable_windows_ansi()


class ClipsterFormatter(logging.Formatter):
    """Render ``[YYYY-MM-DD HH:MM:SS] [LEVEL] message`` with optional colours."""

    def __init__(self, color: bool) -> None:
        super().__init__(datefmt="%Y-%m-%d %H:%M:%S")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        """Format a single record.

        :param record: The record emitted by the logging framework.
        :return: The rendered log line.
        """
        label = _LEVEL_LABELS.get(record.levelname, record.levelname)
        tag = "[{0}]".format(label).ljust(7)
        if self.color:
            tag = "{0}{1}{2}".format(_LEVEL_COLORS.get(label, ""), tag, _RESET)
        message = record.getMessage()
        if record.exc_info:
            message = "{0}\n{1}".format(message, self.formatException(record.exc_info))
        return "[{0}] {1} {2}".format(self.formatTime(record, self.datefmt), tag, message)


def configure(verbose: bool = False, log_to_file: bool = True, level: str | None = None) -> logging.Logger:
    """Configure the ``clipster`` logger tree.

    :param verbose: Force ``DEBUG`` output regardless of ``level``.
    :param log_to_file: Also write a rotating log file into the install directory.
    :param level: Optional level name from the configuration (``INFO``, ``DEBUG``, ...).
    :return: The configured root logger of the application.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    resolved = logging.DEBUG if verbose else getattr(logging, (level or "INFO").upper(), logging.INFO)
    logger.setLevel(resolved)
    logger.propagate = False

    # Under pythonw.exe there is no console and sys.stderr is None; a
    # StreamHandler on it would fail on every single record.
    if sys.stderr is not None:
        console = logging.StreamHandler(stream=sys.stderr)
        console.setFormatter(ClipsterFormatter(color=_supports_color(sys.stderr)))
        logger.addHandler(console)

    if log_to_file:
        try:
            paths.ensure_install_dir()
            target: Path = paths.log_file()
            file_handler = logging.handlers.RotatingFileHandler(
                target, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(ClipsterFormatter(color=False))
            logger.addHandler(file_handler)
        except OSError as exc:  # pragma: no cover - unwritable data directory
            logger.warning("Could not open log file: %s", exc)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger below the application logger.

    :param name: Optional dotted suffix, usually the module name.
    :return: The requested logger instance.
    """
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger("{0}.{1}".format(LOGGER_NAME, name))
