"""System tray integration.

Built on `pystray <https://pypi.org/project/pystray/>`_, which picks a native
backend per platform: ``Shell_NotifyIcon`` on Windows, AppIndicator/GTK or the
XEmbed tray protocol on Linux, and ``NSStatusItem`` on macOS.

The tray is an *optional* feature: when pystray, Pillow or a usable backend is
missing, :meth:`TrayIcon.start` simply returns ``False`` and the application
falls back to the plain status window.  Nothing here may ever raise.

pystray runs its own event loop, so every menu callback arrives on the tray
thread and has to be marshalled onto the Tk thread by the caller.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import APP_SHORT_NAME
from .i18n import Messages
from .logging_setup import get_logger

log = get_logger(__name__)

#: Edge length the icon is scaled to before it is handed to the tray.
ICON_SIZE = 64


def _load_image(icon_path: Path) -> Any:
    """Load and scale the tray icon, falling back to a drawn placeholder.

    :param icon_path: Path of the application PNG.
    :return: A ``PIL.Image.Image`` ready for pystray.
    """
    from PIL import Image

    if icon_path.is_file():
        try:
            image = Image.open(icon_path).convert("RGBA")
            image.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            return image
        except Exception as exc:  # pragma: no cover - broken icon file
            log.debug("Tray icon %s could not be loaded: %s", icon_path, exc)
    return _placeholder_image()


def _placeholder_image() -> Any:
    """Draw a simple red play button as a substitute icon."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, ICON_SIZE - 2, ICON_SIZE - 2), fill=(200, 32, 32, 255))
    draw.polygon(
        [(ICON_SIZE * 0.40, ICON_SIZE * 0.30), (ICON_SIZE * 0.40, ICON_SIZE * 0.70), (ICON_SIZE * 0.72, ICON_SIZE / 2)],
        fill=(255, 255, 255, 255),
    )
    return image


class TrayIcon:
    """The tray icon with its "show window / open folder / quit" menu."""

    def __init__(
        self,
        messages: Messages,
        icon_path: Path,
        on_show: Callable[[], None],
        on_open_folder: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        """
        :param messages: The active translation table.
        :param icon_path: Path of the application PNG.
        :param on_show: Called when the user picks "show window".
        :param on_open_folder: Called when the user picks "open download folder".
        :param on_quit: Called when the user picks "quit".
        """
        self._messages = messages
        self._icon_path = icon_path
        self._on_show = on_show
        self._on_open_folder = on_open_folder
        self._on_quit = on_quit

        self._icon: Any = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._failed = False
        #: ``False`` when the active backend cannot render a menu at all.
        self.has_menu = True
        #: Name of the pystray backend in use, for the log and the about page.
        self.backend = ""

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        """Return ``True`` when the icon is running in the system tray."""
        return self._icon is not None and self._ready.is_set() and not self._failed

    def start(self, timeout: float = 8.0) -> bool:
        """Create the icon and run its event loop in a background thread.

        :param timeout: Seconds to wait for the backend to become ready.
        :return: ``True`` when the tray icon is up.
        """
        try:
            import pystray
        except Exception as exc:
            log.warning("System tray is unavailable (pystray: %s).", exc)
            return False

        try:
            image = _load_image(self._icon_path)
        except Exception as exc:
            log.warning("System tray is unavailable (icon: %s).", exc)
            return False

        self.backend = getattr(pystray.Icon, "__module__", "").rsplit(".", 1)[-1].lstrip("_")
        # pystray's X11 backend sets HAS_MENU = False - it can show an icon but
        # no menu, which would leave the user without a way to quit. Detect that
        # instead of silently shipping a dead icon.
        self.has_menu = bool(getattr(pystray.Icon, "HAS_MENU", True))
        if not self.has_menu:
            log.warning(
                "The '%s' tray backend cannot show a menu, so there is no quit entry. "
                "Install PyGObject and the AppIndicator typelib to get one; "
                "clicking the icon opens the main window in the meantime.",
                self.backend or "unknown",
            )

        menu = pystray.Menu(
            pystray.MenuItem(self._messages["tray_show"], self._handle_show, default=True),
            pystray.MenuItem(self._messages["window_open_folder"], self._handle_open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._messages["window_quit"], self._handle_quit),
        )
        try:
            self._icon = pystray.Icon(
                "youtube-clipster",
                icon=image,
                title=self._messages.get("tray_tooltip", APP_SHORT_NAME),
                menu=menu,
            )
        except Exception as exc:
            log.warning("System tray is unavailable (icon creation: %s).", exc)
            return False

        self._thread = threading.Thread(target=self._run, name="clipster-tray", daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout):
            log.warning("The tray icon did not become ready within %.0f s.", timeout)
            self._failed = True
            return False
        if self._failed:
            return False
        log.info("System tray icon active (backend: %s, menu: %s).",
                 self.backend or "unknown", "yes" if self.has_menu else "no")
        return True

    def _setup(self, icon: Any) -> None:
        """pystray setup callback: make the icon visible and signal readiness."""
        try:
            icon.visible = True
        except Exception as exc:  # pragma: no cover - backend specific
            log.debug("Tray icon could not be made visible: %s", exc)
        self._ready.set()

    def _run(self) -> None:
        """Run the pystray event loop until :meth:`stop` is called."""
        try:
            self._icon.run(setup=self._setup)
        except Exception as exc:
            self._failed = True
            log.warning("Tray icon stopped: %s", exc)
        finally:
            self._ready.set()

    # ------------------------------------------------------------------
    def _handle_show(self, _icon: Any = None, _item: Any = None) -> None:
        """Menu handler for "show window"."""
        self._safe_call(self._on_show)

    def _handle_open_folder(self, _icon: Any = None, _item: Any = None) -> None:
        """Menu handler for "open download folder"."""
        self._safe_call(self._on_open_folder)

    def _handle_quit(self, _icon: Any = None, _item: Any = None) -> None:
        """Menu handler for "quit"."""
        self._safe_call(self._on_quit)

    @staticmethod
    def _safe_call(callback: Callable[[], None]) -> None:
        """Invoke a menu callback without ever killing the tray thread."""
        try:
            callback()
        except Exception:  # pragma: no cover - defensive
            log.exception("Tray menu action failed")

    # ------------------------------------------------------------------
    def set_tooltip(self, text: str) -> None:
        """Update the hover text of the tray icon.

        :param text: The new tooltip.
        :return: None
        """
        if not self.active:
            return
        try:
            self._icon.title = text
        except Exception as exc:  # pragma: no cover - backend specific
            log.debug("Tray tooltip could not be updated: %s", exc)

    def notify(self, message: str) -> bool:
        """Show a desktop notification through the tray icon.

        :param message: The notification body.
        :return: ``True`` when the backend supports and showed it.
        """
        if not self.active:
            return False
        try:
            self._icon.notify(message, APP_SHORT_NAME)
            return True
        except Exception as exc:
            log.debug("Tray notification not supported: %s", exc)
            return False

    def stop(self) -> None:
        """Remove the icon and end its event loop."""
        icon = self._icon
        self._icon = None
        if icon is None:
            return
        try:
            icon.visible = False
        except Exception:  # pragma: no cover
            pass
        try:
            icon.stop()
        except Exception:  # pragma: no cover
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        log.debug("Tray icon removed.")
