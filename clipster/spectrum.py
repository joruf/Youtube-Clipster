"""Equalizer / PCM helpers for Streaming stage visualizations.

Real spectrum mode uses Goertzel band analysis on mono PCM.  :class:`FakeSpectrum`
is the generative fallback when analysis is unavailable.  The ``visualizer`` mode
uses a separate mountain silhouette (see :mod:`clipster.visualizer`).
"""

from __future__ import annotations

import math
import random
import struct
import time
from typing import List, Optional, Sequence, Tuple

#: Sample rate used for analysis and PCM playback.
SAMPLE_RATE = 44100
#: Analysis window length in samples (power of two keeps Goertzel stable).
WINDOW_SAMPLES = 2048
#: Hop size between successive windows (legacy).
HOP_SAMPLES = 1024

#: How often the stream analyser runs a full Goertzel pass (seconds).
SPECTRUM_INTERVAL_SEC = 0.15
#: Default UI tween duration between two analysis snapshots (seconds).
SPECTRUM_TWEEN_SEC = 0.12
#: Ring-buffer length (samples) kept for waveform / RMS / pulse modes.
WAVEFORM_SAMPLES = 1024

#: Center frequencies (Hz) and short UI labels for each equalizer bar.
EQ_BANDS: Tuple[Tuple[int, str], ...] = (
    (31, "31"),
    (62, "62"),
    (125, "125"),
    (250, "250"),
    (500, "500"),
    (1000, "1k"),
    (2000, "2k"),
    (4000, "4k"),
    (8000, "8k"),
    (16000, "16k"),
)

EQ_BAND_HZ: Tuple[int, ...] = tuple(hz for hz, _label in EQ_BANDS)
EQ_BAND_LABELS: Tuple[str, ...] = tuple(label for _hz, label in EQ_BANDS)
EQ_BAR_COUNT = len(EQ_BANDS)


def pcm_s16le_mono_to_floats(pcm: bytes) -> List[float]:
    """Convert little-endian signed 16-bit mono PCM to floats in ``[-1, 1]``."""
    if len(pcm) < 2:
        return []
    count = len(pcm) // 2
    # '<' = little-endian int16
    values = struct.unpack("<{0}h".format(count), pcm[: count * 2])
    scale = 1.0 / 32768.0
    return [sample * scale for sample in values]


def goertzel_power(samples: Sequence[float], frequency_hz: float, sample_rate: int = SAMPLE_RATE) -> float:
    """Return the Goertzel power at ``frequency_hz`` for ``samples``.

    :param samples: Mono float samples.
    :param frequency_hz: Target frequency in Hertz.
    :param sample_rate: Sample rate of ``samples``.
    :return: Non-negative power estimate.
    """
    n = len(samples)
    if n < 8 or frequency_hz <= 0 or frequency_hz >= sample_rate * 0.5:
        return 0.0
    k = int(0.5 + (n * frequency_hz) / float(sample_rate))
    omega = (2.0 * math.pi * k) / float(n)
    coeff = 2.0 * math.cos(omega)
    s_prev = 0.0
    s_prev2 = 0.0
    for sample in samples:
        s = sample + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s
    power = s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2
    return max(0.0, power / float(n))


def band_levels_from_samples(
    samples: Sequence[float],
    *,
    sample_rate: int = SAMPLE_RATE,
    previous: Sequence[float] | None = None,
    attack: float = 0.55,
    release: float = 0.18,
) -> List[float]:
    """Return smoothed 0..1 levels for each :data:`EQ_BAND_HZ` center.

    :param samples: Mono float window.
    :param sample_rate: Sample rate.
    :param previous: Optional previous levels for attack/release smoothing.
    :param attack: How fast levels rise (0..1).
    :param release: How fast levels fall (0..1).
    :return: One level per equalizer band.
    """
    raw: List[float] = []
    for frequency in EQ_BAND_HZ:
        power = goertzel_power(samples, float(frequency), sample_rate)
        # Log compression so quiet and loud passages both move the bars.
        level = math.log10(1.0 + power * 80.0) / math.log10(81.0)
        raw.append(max(0.0, min(1.0, level)))

    if previous is None or len(previous) != len(raw):
        return raw

    smoothed: List[float] = []
    for index, value in enumerate(raw):
        prior = float(previous[index])
        if value >= prior:
            smoothed.append(prior + (value - prior) * attack)
        else:
            smoothed.append(prior + (value - prior) * release)
    return smoothed


def band_levels_from_pcm(
    pcm: bytes,
    *,
    previous: Sequence[float] | None = None,
) -> List[float]:
    """Analyze a s16le mono PCM buffer and return equalizer levels."""
    samples = pcm_s16le_mono_to_floats(pcm)
    if len(samples) < WINDOW_SAMPLES // 4:
        return list(previous) if previous is not None else [0.0] * EQ_BAR_COUNT
    if len(samples) > WINDOW_SAMPLES:
        samples = samples[-WINDOW_SAMPLES:]
    return band_levels_from_samples(samples, previous=previous)


def ease_smoothstep(t: float) -> float:
    """Map ``t`` in ``[0, 1]`` through a smoothstep ease (slow in / slow out)."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def lerp_levels(
    start: Sequence[float],
    end: Sequence[float],
    t: float,
    *,
    count: int = EQ_BAR_COUNT,
) -> List[float]:
    """Linearly interpolate band levels; ``t`` is clamped to ``[0, 1]``."""
    amount = 0.0 if t <= 0.0 else (1.0 if t >= 1.0 else float(t))
    result: List[float] = []
    for index in range(count):
        a = float(start[index]) if index < len(start) else 0.0
        b = float(end[index]) if index < len(end) else 0.0
        value = a + (b - a) * amount
        result.append(max(0.0, min(1.0, value)))
    return result


def levels_changed(a: Sequence[float], b: Sequence[float], *, epsilon: float = 0.015) -> bool:
    """Return ``True`` when any band differs by more than ``epsilon``."""
    if len(a) != len(b):
        return True
    for left, right in zip(a, b):
        if abs(float(left) - float(right)) > epsilon:
            return True
    return False


class SpectrumTween:
    """Ease displayed equalizer bars between sparse analysis snapshots (legacy)."""

    def __init__(self, duration_sec: float = SPECTRUM_TWEEN_SEC) -> None:
        self.duration_sec = max(0.05, float(duration_sec))
        self._from = [0.0] * EQ_BAR_COUNT
        self._to = [0.0] * EQ_BAR_COUNT
        self._started_at = time.monotonic()
        self._target: List[float] = [0.0] * EQ_BAR_COUNT

    def reset(self, levels: Optional[Sequence[float]] = None) -> None:
        """Jump to ``levels`` (or zero) without animation."""
        values = [0.0] * EQ_BAR_COUNT if levels is None else list(levels)[:EQ_BAR_COUNT]
        while len(values) < EQ_BAR_COUNT:
            values.append(0.0)
        self._from = list(values)
        self._to = list(values)
        self._target = list(values)
        self._started_at = time.monotonic()

    def set_target(self, levels: Sequence[float], *, now: Optional[float] = None) -> bool:
        """Start a tween from the current display toward ``levels``.

        :return: ``True`` when a new tween was started.
        """
        target = [max(0.0, min(1.0, float(value))) for value in list(levels)[:EQ_BAR_COUNT]]
        while len(target) < EQ_BAR_COUNT:
            target.append(0.0)
        if not levels_changed(self._target, target):
            return False
        clock = time.monotonic() if now is None else float(now)
        self._from = self.current(now=clock)
        self._to = target
        self._target = target
        self._started_at = clock
        return True

    def current(self, *, now: Optional[float] = None) -> List[float]:
        """Return eased levels for the current time."""
        clock = time.monotonic() if now is None else float(now)
        t = (clock - self._started_at) / self.duration_sec
        return lerp_levels(self._from, self._to, ease_smoothstep(t))


class FakeSpectrum:
    """Generative equalizer levels driven by playback state.

    While playing, each bar follows a local oscillator plus a light random walk
    so the display feels alive at ~15–30 fps without PCM analysis.  When paused
    or stopped the envelope decays and bars settle toward zero.
    """

    def __init__(self, bar_count: int = EQ_BAR_COUNT, *, seed: Optional[int] = None) -> None:
        self.bar_count = max(1, int(bar_count))
        self._rng = random.Random(seed)
        self._phase = [self._rng.uniform(0.0, 2.0 * math.pi) for _ in range(self.bar_count)]
        self._speed = [self._rng.uniform(1.6, 3.4) for _ in range(self.bar_count)]
        self._walk = [self._rng.uniform(0.35, 0.75) for _ in range(self.bar_count)]
        self._levels = [0.0] * self.bar_count
        self._energy = 0.0
        self._last_t: Optional[float] = None
        self._playing = False

    def reset(self) -> None:
        """Jump all bars to zero and clear playback state."""
        self._levels = [0.0] * self.bar_count
        self._energy = 0.0
        self._last_t = None
        self._playing = False

    def set_playing(self, playing: bool) -> None:
        """Remember whether audio is currently playing."""
        self._playing = bool(playing)

    @property
    def playing(self) -> bool:
        """Return the last playback flag passed to :meth:`tick` / :meth:`set_playing`."""
        return self._playing

    def current(self) -> List[float]:
        """Return the latest bar levels without advancing time."""
        return list(self._levels)

    def tick(self, *, now: Optional[float] = None, playing: Optional[bool] = None) -> List[float]:
        """Advance the animation by one frame and return 0..1 bar levels.

        :param now: Optional monotonic clock (for tests).
        :param playing: When set, updates the playing flag for this tick.
        :return: One level per bar.
        """
        if playing is not None:
            self._playing = bool(playing)
        clock = time.monotonic() if now is None else float(now)
        if self._last_t is None:
            dt = 1.0 / 24.0
        else:
            dt = max(0.0, min(0.12, clock - self._last_t))
        self._last_t = clock

        if self._playing:
            self._energy = min(1.0, self._energy + dt * 4.0)
        else:
            self._energy = max(0.0, self._energy - dt * 2.8)

        span = max(1, self.bar_count - 1)
        for index in range(self.bar_count):
            if self._playing and self._energy > 0.01:
                self._phase[index] += self._speed[index] * dt
                # Slight mid-band emphasis for a classic graphic-EQ silhouette.
                mid = math.sin(math.pi * index / float(span))
                bias = 0.28 + 0.42 * mid
                osc = 0.5 + 0.5 * math.sin(self._phase[index])
                self._walk[index] += self._rng.uniform(-0.55, 0.55) * dt
                self._walk[index] = max(0.15, min(0.95, self._walk[index]))
                target = (0.42 * osc + 0.38 * bias + 0.20 * self._walk[index]) * self._energy
                target = max(0.06, min(1.0, target))
                rate = 10.0 if target >= self._levels[index] else 6.5
                self._levels[index] += (target - self._levels[index]) * min(1.0, rate * dt)
            else:
                self._levels[index] *= max(0.0, 1.0 - 5.5 * dt)
                if self._levels[index] < 0.015:
                    self._levels[index] = 0.0

        return list(self._levels)
