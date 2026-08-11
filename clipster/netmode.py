"""What the connection is allowed to cost: streaming or only local files.

On a phone the Streaming tab has two very different appetites.  Over Wi-Fi it
resolves songs from YouTube and pulls the audio down while it plays; on mobile
data that is somebody's monthly allowance.  This module holds the one rule that
decides between them, so the Tk page, the web API and the media routes cannot
drift into three slightly different answers.

The rule needs two inputs and nothing else:

* what the user asked for - :attr:`~clipster.config.Config.playback_on_mobile`
  and the manual :attr:`~clipster.config.Config.playback_local_only` switch;
* what the connection currently looks like, as reported by the interface that
  is actually on it (the browser knows; a desktop PC has no opinion and says
  nothing).

Nothing here reaches the network or the file system: it is a decision table,
which is exactly why it can be tested without either.
"""

from __future__ import annotations

from typing import Any, Tuple

#: Stream from YouTube even on mobile data.
MOBILE_STREAM = "stream"
#: Play only what is already in the download folder while on mobile data.
MOBILE_LOCAL = "local"
#: Ask before the first stream on a mobile connection.
MOBILE_ASK = "ask"

#: Every accepted value of ``playback_on_mobile``, in the order a menu shows them.
MOBILE_MODES: Tuple[str, ...] = (MOBILE_STREAM, MOBILE_LOCAL, MOBILE_ASK)

#: Default: behave as before and stream. The saving only starts when it is asked
#: for - a program that silently stops playing music is worse than one that
#: costs data the user already expected to spend.
DEFAULT_MOBILE_MODE = MOBILE_STREAM

#: Connection descriptions that mean "this traffic is metered".  The first two
#: are what ``navigator.connection.type`` reports; ``effectiveType`` values such
#: as ``3g`` arrive as their own names and are treated the same way.
_METERED = frozenset({"cellular", "wimax", "2g", "3g", "4g", "5g"})

#: What an interface reports when it has no idea - a desktop, or a browser
#: without the Network Information API.
UNKNOWN = ""


def normalize_mode(value: Any) -> str:
    """Return a usable ``playback_on_mobile`` value.

    :param value: Whatever was stored in the configuration.
    :return: One of :data:`MOBILE_MODES`.
    """
    text = str(value or "").strip().lower()
    return text if text in MOBILE_MODES else DEFAULT_MOBILE_MODE


def normalize_connection(value: Any) -> str:
    """Return a usable connection description.

    :param value: What the interface reported, if anything.
    :return: The lower-case description, or :data:`UNKNOWN`.
    """
    return str(value or "").strip().lower()


def is_metered(connection: Any) -> bool:
    """Return ``True`` when this connection costs data volume.

    An unknown connection is *not* metered: guessing "mobile" would stop the
    music on every desktop that never reports anything.

    :param connection: What the interface reported.
    :return: Whether traffic on it should be avoided.
    """
    return normalize_connection(connection) in _METERED


def local_only(config: Any, connection: Any = UNKNOWN) -> bool:
    """Return ``True`` when playback must stay inside the download folder.

    :param config: The configuration to read the two settings off.
    :param connection: What the interface reported about the connection.
    :return: Whether streaming is currently forbidden.
    """
    if bool(getattr(config, "playback_local_only", False)):
        return True
    if not is_metered(connection):
        return False
    return normalize_mode(getattr(config, "playback_on_mobile", DEFAULT_MOBILE_MODE)) == MOBILE_LOCAL


def should_ask(config: Any, connection: Any = UNKNOWN) -> bool:
    """Return ``True`` when the user wants to be asked before streaming.

    The manual switch wins: somebody who turned local-only on has already
    answered the question and must not be asked again.

    :param config: The configuration to read the two settings off.
    :param connection: What the interface reported about the connection.
    :return: Whether to put the decision to the user.
    """
    if bool(getattr(config, "playback_local_only", False)):
        return False
    if not is_metered(connection):
        return False
    return normalize_mode(getattr(config, "playback_on_mobile", DEFAULT_MOBILE_MODE)) == MOBILE_ASK
