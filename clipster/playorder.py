"""In what order the Streaming queue is played: shuffle, repeat, and the end.

Two things play a queue - the Tk page on a desktop and the headless session the
phone drives - and both have to answer the same question the same way: *what
comes after this song?*  That answer lives here once, so a mode cannot work on
one platform and quietly do nothing on the other.

Two rules are worth stating outright, because they are the ones that surprise:

* **Repeat-one only repeats a song that ended by itself.**  Pressing *next* means
  "something else", never the same song again.
* **Shuffle draws from a bag, not from a hat.**  A plain random pick replays the
  same song within minutes and buries others for an hour.  Every row goes into a
  bag; only when the bag is empty does repeat decide whether there is another
  round at all.

Nothing here touches a player, a widget or a file - it is arithmetic on a queue
length and a position, which is why it can be tested exhaustively.
"""

from __future__ import annotations

import random
from typing import List, Optional

#: Play the queue through once and stop.
REPEAT_OFF = "off"
#: Start the queue again from the top.
REPEAT_ALL = "all"
#: Play the same song again when it ends by itself.
REPEAT_ONE = "one"

#: The order the repeat button steps through.
REPEAT_ORDER = (REPEAT_OFF, REPEAT_ALL, REPEAT_ONE)


def normalize_repeat(value: object) -> str:
    """Return a usable repeat mode.

    :param value: Whatever was stored in the configuration.
    :return: One of :data:`REPEAT_ORDER`.
    """
    wanted = str(value or "").strip().lower()
    return wanted if wanted in REPEAT_ORDER else REPEAT_OFF


class PlayOrder:
    """Decides which queue row plays next.

    The caller owns the queue and the position; this owns only the two modes and
    the shuffle bag.
    """

    def __init__(self, *, shuffle: bool = False, repeat: str = REPEAT_OFF) -> None:
        """
        :param shuffle: Whether to play in random order.
        :param repeat: One of :data:`REPEAT_ORDER`.
        """
        self.shuffle = bool(shuffle)
        self.repeat = normalize_repeat(repeat)
        self._bag: List[int] = []
        self._started = False

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Start a fresh random round, after the queue changed.

        :return: None
        """
        self._bag = []
        self._started = False

    def cycle_repeat(self) -> str:
        """Step to the next repeat mode.

        :return: The mode now in force.
        """
        position = REPEAT_ORDER.index(self.repeat)
        self.repeat = REPEAT_ORDER[(position + 1) % len(REPEAT_ORDER)]
        return self.repeat

    def set_shuffle(self, enabled: bool) -> None:
        """Turn random order on or off and start a fresh round.

        :param enabled: Whether to play in random order.
        :return: None
        """
        self.shuffle = bool(enabled)
        self.reset()

    def set_repeat(self, mode: str) -> None:
        """Set the repeat mode.

        :param mode: One of :data:`REPEAT_ORDER`; anything else means off.
        :return: None
        """
        self.repeat = normalize_repeat(mode)

    # ------------------------------------------------------------------
    def next_index(self, count: int, current: int, *, automatic: bool) -> Optional[int]:
        """Return the row to play after ``current``.

        :param count: How many rows the queue holds.
        :param current: The row playing now, or ``-1`` for none.
        :param automatic: ``True`` when the song ended on its own, ``False`` when
            somebody pressed *next*.
        :return: The row to play, or ``None`` when the queue is through.
        """
        if count <= 0:
            return None
        if automatic and self.repeat == REPEAT_ONE:
            return max(0, current)
        if self.shuffle:
            return self._draw(count, current)
        following = current + 1
        if following < count:
            return following
        return 0 if self.repeat == REPEAT_ALL else None

    def _draw(self, count: int, current: int) -> Optional[int]:
        """Return the next random row, playing each one before any repeats.

        :param count: How many rows the queue holds.
        :param current: The row playing now.
        :return: The row to play, or ``None`` when the round is over.
        """
        self._bag = [index for index in self._bag if index < count]
        if not self._bag:
            if self._started and self.repeat != REPEAT_ALL:
                return None
            self._refill(count, current)
        if not self._bag:
            # A queue of one: repeat it, or stop when repeat is off.
            return current if self.repeat != REPEAT_OFF else None
        return self._bag.pop()

    def _refill(self, count: int, current: int) -> None:
        """Put every row except the current one back into the bag, shuffled.

        :param count: How many rows the queue holds.
        :param current: The row playing now, which nobody wants twice in a row.
        :return: None
        """
        self._bag = [index for index in range(count) if index != current]
        random.shuffle(self._bag)
        self._started = True
