"""Unit tests for equalizer helpers and the generative FakeSpectrum."""

from __future__ import annotations

import math
import struct

from clipster.spectrum import (
    EQ_BAR_COUNT,
    EQ_BAND_HZ,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    FakeSpectrum,
    band_levels_from_pcm,
    band_levels_from_samples,
    goertzel_power,
    pcm_s16le_mono_to_floats,
)


def _sine_pcm(frequency_hz: float, samples: int = WINDOW_SAMPLES, amplitude: float = 0.6) -> bytes:
    """Build mono s16le PCM for a pure sine at ``frequency_hz``."""
    out = bytearray()
    for index in range(samples):
        value = amplitude * math.sin(2.0 * math.pi * frequency_hz * index / float(SAMPLE_RATE))
        sample = max(-32767, min(32767, int(value * 32767.0)))
        out.extend(struct.pack("<h", sample))
    return bytes(out)


def test_pcm_s16le_converts_to_floats() -> None:
    pcm = struct.pack("<hhh", 0, 16384, -16384)
    floats = pcm_s16le_mono_to_floats(pcm)
    assert len(floats) == 3
    assert abs(floats[0]) < 1e-6
    assert abs(floats[1] - 0.5) < 0.01
    assert abs(floats[2] + 0.5) < 0.01


def test_goertzel_peaks_near_target_frequency() -> None:
    samples = pcm_s16le_mono_to_floats(_sine_pcm(1000.0))
    near = goertzel_power(samples, 1000.0)
    far = goertzel_power(samples, 8000.0)
    assert near > far * 4


def test_band_levels_peak_on_matching_khz_band() -> None:
    # 1 kHz sine should light the 1000 Hz band more than distant bands.
    levels = band_levels_from_pcm(_sine_pcm(1000.0))
    assert len(levels) == EQ_BAR_COUNT
    peak_index = EQ_BAND_HZ.index(1000)
    assert levels[peak_index] == max(levels)
    assert levels[peak_index] > 0.2
    assert levels[EQ_BAND_HZ.index(16000)] < levels[peak_index] * 0.6


def test_band_levels_smooth_with_previous() -> None:
    quiet = band_levels_from_samples([0.0] * WINDOW_SAMPLES)
    loud = band_levels_from_pcm(_sine_pcm(250.0), previous=quiet)
    assert loud[EQ_BAND_HZ.index(250)] > quiet[EQ_BAND_HZ.index(250)]


def test_lerp_levels_midpoint() -> None:
    from clipster.spectrum import lerp_levels

    start = [0.0] * EQ_BAR_COUNT
    end = [1.0] * EQ_BAR_COUNT
    mid = lerp_levels(start, end, 0.5)
    assert all(abs(value - 0.5) < 1e-9 for value in mid)


def test_ease_smoothstep_endpoints() -> None:
    from clipster.spectrum import ease_smoothstep

    assert ease_smoothstep(0.0) == 0.0
    assert ease_smoothstep(1.0) == 1.0
    assert 0.4 < ease_smoothstep(0.5) < 0.6


def test_spectrum_tween_eases_between_targets() -> None:
    from clipster.spectrum import SpectrumTween

    tween = SpectrumTween(duration_sec=1.0)
    tween.reset([0.0] * EQ_BAR_COUNT)
    target = [0.0] * EQ_BAR_COUNT
    target[3] = 1.0
    assert tween.set_target(target, now=10.0) is True
    assert tween.set_target(target, now=10.1) is False
    mid = tween.current(now=10.5)
    assert 0.4 < mid[3] < 0.6
    end = tween.current(now=11.0)
    assert abs(end[3] - 1.0) < 1e-9


def test_fake_spectrum_moves_while_playing() -> None:
    eq = FakeSpectrum(seed=7)
    first = eq.tick(now=1.0, playing=True)
    assert len(first) == EQ_BAR_COUNT
    assert all(0.0 <= value <= 1.0 for value in first)
    later = first
    for step in range(1, 25):
        later = eq.tick(now=1.0 + step * (1.0 / 24.0), playing=True)
    assert max(later) > 0.15
    assert later != first


def test_fake_spectrum_settles_when_paused() -> None:
    eq = FakeSpectrum(seed=3)
    for step in range(20):
        eq.tick(now=10.0 + step * 0.04, playing=True)
    assert max(eq.current()) > 0.1
    settled = [1.0]
    for step in range(40):
        settled = eq.tick(now=20.0 + step * 0.04, playing=False)
    assert max(settled) < 0.05


def test_fake_spectrum_reset_clears_levels() -> None:
    eq = FakeSpectrum(seed=1)
    for step in range(15):
        eq.tick(now=5.0 + step * 0.04, playing=True)
    eq.reset()
    assert eq.current() == [0.0] * EQ_BAR_COUNT
    assert eq.playing is False
