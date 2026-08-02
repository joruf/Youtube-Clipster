"""Streaming audio-stage visualizer modes and helpers.

Mode ids are persisted as ``config.discover_visualizer``.  Drawing happens on
the Discover page canvas; PCM analysis (when needed) is owned by the player.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

#: Real Goertzel spectrum bars (PCM), with generative fallback when unavailable.
VIZ_SPECTRUM = "spectrum"
#: Oscilloscope line from recent PCM amplitude.
VIZ_WAVEFORM = "waveform"
#: Expanding ring driven by broadband energy.
VIZ_PULSE = "pulse"
#: Track thumbnail (large); optional light motion.
VIZ_COVER = "cover"
#: Minimal idle / text placeholder — no bars.
VIZ_TEXT = "text"
#: Dense generative mountain / mirrored silhouette (distinct from spectrum).
VIZ_VISUALIZER = "visualizer"
#: Blank stage; no visualization CPU work.
VIZ_OFF = "off"

#: Combobox order: Off, Text only, then visual modes.
VISUALIZER_MODES: Tuple[str, ...] = (
    VIZ_OFF,
    VIZ_TEXT,
    VIZ_WAVEFORM,
    VIZ_COVER,
    VIZ_PULSE,
    VIZ_SPECTRUM,
    VIZ_VISUALIZER,
)

#: Default stage mode for new installs.
DEFAULT_VISUALIZER = VIZ_PULSE

#: Locale key for each mode (``discover_viz_<suffix>``).
VISUALIZER_LOCALE_KEYS: Dict[str, str] = {
    VIZ_SPECTRUM: "discover_viz_spectrum",
    VIZ_WAVEFORM: "discover_viz_waveform",
    VIZ_PULSE: "discover_viz_pulse",
    VIZ_COVER: "discover_viz_cover",
    VIZ_TEXT: "discover_viz_text",
    VIZ_VISUALIZER: "discover_viz_visualizer",
    VIZ_OFF: "discover_viz_off",
}

#: Bar count for the generative mountain visualizer (denser than EQ bands).
MOUNTAIN_BAR_COUNT = 48

_PCM_MODES = frozenset({VIZ_SPECTRUM, VIZ_WAVEFORM, VIZ_PULSE})
_ANIMATED_MODES = frozenset(
    {VIZ_SPECTRUM, VIZ_WAVEFORM, VIZ_PULSE, VIZ_VISUALIZER, VIZ_COVER}
)


def normalize_visualizer(value: str | None) -> str:
    """Return a known mode id, or :data:`DEFAULT_VISUALIZER`.

    Legacy ``rms`` / ``loudness`` values migrate to :data:`VIZ_WAVEFORM`.
    """
    key = (value or "").strip().lower()
    if key in VISUALIZER_MODES:
        return key
    # Legacy / friendly aliases (including removed modes).
    aliases = {
        "eq": VIZ_SPECTRUM,
        "equalizer": VIZ_SPECTRUM,
        "fake": VIZ_VISUALIZER,
        "oscope": VIZ_WAVEFORM,
        "rms": VIZ_WAVEFORM,
        "loudness": VIZ_WAVEFORM,
        "beat": VIZ_PULSE,
        "thumbnail": VIZ_COVER,
        "none": VIZ_OFF,
        "blank": VIZ_OFF,
    }
    return aliases.get(key, DEFAULT_VISUALIZER)


def visualizer_needs_pcm(mode: str) -> bool:
    """Return ``True`` when the mode benefits from a live PCM analyser."""
    return normalize_visualizer(mode) in _PCM_MODES


def visualizer_animates(mode: str) -> bool:
    """Return ``True`` when the stage should run a redraw timer."""
    return normalize_visualizer(mode) in _ANIMATED_MODES


def visualizer_locale_key(mode: str) -> str:
    """Return the messages key for ``mode``'s display name."""
    return VISUALIZER_LOCALE_KEYS[normalize_visualizer(mode)]


def visualizer_mode_choices(messages: object) -> Tuple[Tuple[str, ...], Dict[str, str], Dict[str, str]]:
    """Build Combobox labels and bidirectional maps for every visualizer mode.

    :param messages: A mapping/``Messages`` object supporting ``messages[key]``.
    :return: ``(labels, label_to_mode, mode_to_label)`` where ``labels`` is an
        ordered tuple of non-empty display strings suitable for
        ``ttk.Combobox(values=...)``.
    """
    labels: List[str] = []
    label_to_mode: Dict[str, str] = {}
    mode_to_label: Dict[str, str] = {}
    for mode in VISUALIZER_MODES:
        label = str(messages[visualizer_locale_key(mode)]).strip()  # type: ignore[index]
        if not label:
            label = mode
        # Keep first label on collision so Combobox values stay unique strings.
        if label not in label_to_mode:
            labels.append(label)
            label_to_mode[label] = mode
        mode_to_label[mode] = label
    return tuple(labels), label_to_mode, mode_to_label


def downsample_waveform(samples: Sequence[float], points: int) -> List[float]:
    """Reduce ``samples`` to ``points`` peaks for an oscilloscope polyline.

    :param samples: Mono amplitudes roughly in ``[-1, 1]``.
    :param points: Desired vertex count (>= 2).
    :return: List of floats in ``[-1, 1]``.
    """
    count = max(2, int(points))
    if not samples:
        return [0.0] * count
    length = len(samples)
    if length <= count:
        out = [max(-1.0, min(1.0, float(value))) for value in samples]
        while len(out) < count:
            out.append(0.0)
        return out[:count]
    result: List[float] = []
    for index in range(count):
        start = int(index * length / count)
        end = max(start + 1, int((index + 1) * length / count))
        chunk = samples[start:end]
        # Peak in the bin keeps the display lively.
        peak = max(chunk, key=lambda value: abs(float(value)))
        result.append(max(-1.0, min(1.0, float(peak))))
    return result


def rms_of_samples(samples: Sequence[float]) -> float:
    """Return RMS loudness of ``samples`` clamped to ``[0, 1]``."""
    if not samples:
        return 0.0
    total = 0.0
    for value in samples:
        sample = float(value)
        total += sample * sample
    return max(0.0, min(1.0, math.sqrt(total / float(len(samples)))))


def peak_of_samples(samples: Sequence[float]) -> float:
    """Return peak absolute amplitude clamped to ``[0, 1]``."""
    if not samples:
        return 0.0
    peak = 0.0
    for value in samples:
        peak = max(peak, abs(float(value)))
    return max(0.0, min(1.0, peak))


def generative_waveform(
    points: int,
    *,
    energy: float,
    phase: float,
) -> List[float]:
    """Build a lively oscilloscope polyline when live PCM is unavailable.

    :param points: Vertex count (>= 2).
    :param energy: Envelope in ``[0, 1]`` (typically from :class:`FakeSpectrum`).
    :param phase: Time / phase driver (e.g. ``time.monotonic()``).
    :return: Amplitudes in ``[-1, 1]``.
    """
    count = max(2, int(points))
    level = max(0.0, min(1.0, float(energy)))
    if level < 0.02:
        return [0.0] * count
    out: List[float] = []
    for index in range(count):
        x = index / float(count - 1)
        value = level * (
            0.55 * math.sin(phase * 8.0 + x * 10.0)
            + 0.30 * math.sin(phase * 13.0 + x * 22.0)
            + 0.15 * math.sin(phase * 3.2 + x * 4.0)
        )
        out.append(max(-1.0, min(1.0, value)))
    return out


def generative_mountain_levels(
    bars: int = MOUNTAIN_BAR_COUNT,
    *,
    energy: float,
    phase: float,
) -> List[float]:
    """Build a dense, centre-peaked mountain silhouette for visualizer mode.

    Unlike spectrum EQ bands, this is always generative and intentionally denser
    so the stage never looks identical to :data:`VIZ_SPECTRUM`.

    :param bars: Column count (>= 4); odd counts are fine.
    :param energy: Envelope in ``[0, 1]``.
    :param phase: Time / phase driver.
    :return: Levels in ``[0, 1]`` (left-to-right, symmetric envelope).
    """
    count = max(4, int(bars))
    level = max(0.0, min(1.0, float(energy)))
    if level < 0.02:
        return [0.0] * count
    mid = (count - 1) / 2.0
    out: List[float] = []
    for index in range(count):
        # Distance from centre → soft Gaussian-ish falloff (mirrored look).
        dist = abs(index - mid) / mid
        envelope = math.exp(-2.8 * dist * dist)
        ripple = 0.55 + 0.45 * math.sin(phase * 5.5 + index * 0.55)
        swell = 0.70 + 0.30 * math.sin(phase * 2.1 + dist * 6.0)
        value = level * envelope * ripple * swell
        out.append(max(0.0, min(1.0, value)))
    return out
