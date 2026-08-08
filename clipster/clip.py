"""One section out of a video: time parsing, validation and file naming.

The navigation window offers two small fields, "from" and "to".  Everything
that has to happen with those two strings lives here and nowhere else: the
window only renders them, :mod:`clipster.downloader` only turns the result into
yt-dlp options.  That keeps the rules testable without a display.

A section is deliberately *not* a configuration setting.  It belongs to one
download - the next link is a whole video again unless the user says otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

#: Message key for a field that is not a time at all.
ERROR_TIME = "clip_error_time"
#: Message key for an end that is not after the start.
ERROR_ORDER = "clip_error_order"
#: Message key for a start that lies behind the end of the video.
ERROR_RANGE = "clip_error_range"

#: ``90``, ``1:30``, ``1:02:03``, each with an optional fraction.  Written out
#: rather than split on ``:`` so that ``1e3``, ``inf`` and ``-5`` - all of which
#: :func:`float` would happily accept - are refused.
_TIME = re.compile(r"^(\d+)(?::(\d{1,2}))?(?::(\d{1,2}))?(?:\.(\d{1,3}))?$")


def parse_time(text: str) -> Optional[float]:
    """Return ``text`` as seconds.

    Accepted are plain seconds (``90``), minutes and seconds (``1:30``) and
    hours, minutes and seconds (``1:02:03``), each with an optional fraction
    (``1:30.5``, ``1:30,5``).  A minute or second field of ``60`` or more is a
    typo, not a time, and is refused instead of being carried over.

    :param text: What the user typed; empty or blank means "not set".
    :return: The time in seconds, or ``None`` when the field is empty or unusable.
    """
    cleaned = "".join((text or "").split()).replace(",", ".")
    if not cleaned:
        return None
    match = _TIME.match(cleaned)
    if match is None:
        return None

    numbers = [int(group) for group in match.groups()[:3] if group is not None]
    if len(numbers) == 1:
        hours, minutes, seconds = 0, 0, numbers[0]
    elif len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]
    else:
        hours, minutes, seconds = numbers
    if len(numbers) > 1 and seconds > 59:
        return None
    if len(numbers) > 2 and minutes > 59:
        return None

    total = float(hours * 3600 + minutes * 60 + seconds)
    fraction = match.group(4)
    if fraction:
        total += float("0." + fraction)
    return total


def format_time(seconds: float) -> str:
    """Return ``seconds`` as ``m:ss``, or ``h:mm:ss`` from one hour on.

    :param seconds: A number of seconds; fractions are dropped.
    :return: The clock text, always with two digit seconds.
    """
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, remainder = divmod(rest, 60)
    if hours:
        return "{0}:{1:02d}:{2:02d}".format(hours, minutes, remainder)
    return "{0}:{1:02d}".format(minutes, remainder)


@dataclass(frozen=True)
class ClipRange:
    """The section the user asked for, in seconds from the start of the video."""

    #: Where the section begins.
    start: float
    #: Where it ends, or ``None`` when it runs to the end of the video.
    end: Optional[float] = None

    @property
    def length(self) -> Optional[float]:
        """Return the length of the section, or ``None`` when the end is open."""
        if self.end is None:
            return None
        return max(0.0, self.end - self.start)

    def key(self) -> str:
        """Return a canonical id for the download list.

        Two runs of the same link only count as the same download when they cut
        the same piece out of it, so this string goes into the history entry and
        is compared there.

        :return: For example ``83-165``, or ``83-`` for an open end.
        """
        end = "" if self.end is None else "{0:g}".format(round(self.end, 3))
        return "{0:g}-{1}".format(round(self.start, 3), end)

    def label(self) -> str:
        """Return the section for the user, e.g. ``1:23 - 2:45``."""
        end = "" if self.end is None else format_time(self.end)
        return "{0} - {1}".format(format_time(self.start), end).strip(" -")

    def marker(self) -> str:
        """Return the part that is added to the file name.

        Colons are not allowed in file names on Windows, so the clock separator
        becomes a hyphen: ``1-23_2-45``.

        :return: The marker without the surrounding brackets.
        """
        end = "end" if self.end is None else format_time(self.end)
        return "{0}_{1}".format(format_time(self.start), end).replace(":", "-")


def parse_range(
    start_text: str, end_text: str, duration: int = 0
) -> Tuple[Optional[ClipRange], str]:
    """Turn the two navigation window fields into a section.

    An end beyond the video is clamped rather than refused - typing a generous
    end to mean "until it is over" is a reasonable thing to do.  A start beyond
    the video is refused, because there is nothing there to cut.

    :param start_text: The "from" field; empty means "from the beginning".
    :param end_text: The "to" field; empty means "until the end".
    :param duration: Length of the video in seconds, ``0`` when unknown.
    :return: ``(section, error)``.  ``section`` is ``None`` when the whole video
        was asked for or the input was rejected; ``error`` is one of the
        ``clip_error_*`` message keys and empty when the input was fine.
    """
    raw_start = "".join((start_text or "").split())
    raw_end = "".join((end_text or "").split())
    if not raw_start and not raw_end:
        return None, ""

    start = 0.0
    if raw_start:
        parsed = parse_time(raw_start)
        if parsed is None:
            return None, ERROR_TIME
        start = parsed

    end: Optional[float] = None
    if raw_end:
        end = parse_time(raw_end)
        if end is None:
            return None, ERROR_TIME

    if duration > 0:
        if start >= duration:
            return None, ERROR_RANGE
        if end is None or end > duration:
            end = float(duration)

    if end is not None and end <= start:
        return None, ERROR_ORDER
    if start <= 0 and (end is None or (duration > 0 and end >= duration)):
        # Beginning to end is the whole video - no reason to cut anything.
        return None, ""
    return ClipRange(start=start, end=end), ""


def output_template(template: str, section: ClipRange) -> str:
    """Return ``template`` with the section marked in the file name.

    Without this a clip would land on the file name of the full download: yt-dlp
    is told not to overwrite anything, so the section would silently be skipped
    and the full video handed back instead.

    :param template: The configured yt-dlp output template.
    :param section: The section being downloaded.
    :return: The template with ``[1-23_2-45]`` in front of the extension.
    """
    marker = " [{0}]".format(section.marker())
    extension = ".%(ext)s"
    index = template.rfind(extension)
    if index < 0:
        return template + marker
    return template[:index] + marker + template[index:]
