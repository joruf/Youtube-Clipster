"""Unit tests for Streaming stage visualizer modes and helpers."""

from __future__ import annotations

from pathlib import Path

from clipster.config import Config
from clipster.visualizer import (
    DEFAULT_VISUALIZER,
    MOUNTAIN_BAR_COUNT,
    VISUALIZER_MODES,
    VIZ_OFF,
    VIZ_PULSE,
    VIZ_SPECTRUM,
    VIZ_TEXT,
    VIZ_VISUALIZER,
    VIZ_WAVEFORM,
    downsample_waveform,
    generative_mountain_levels,
    normalize_visualizer,
    peak_of_samples,
    rms_of_samples,
    visualizer_animates,
    visualizer_locale_key,
    visualizer_mode_choices,
    visualizer_needs_pcm,
)


def test_visualizer_modes_order_and_default() -> None:
    assert VISUALIZER_MODES == (
        VIZ_OFF,
        VIZ_TEXT,
        VIZ_WAVEFORM,
        "cover",
        "pulse",
        VIZ_SPECTRUM,
        VIZ_VISUALIZER,
    )
    assert len(VISUALIZER_MODES) == 7
    assert DEFAULT_VISUALIZER == VIZ_PULSE
    assert "rms" not in VISUALIZER_MODES


def test_normalize_visualizer_aliases_and_default() -> None:
    assert normalize_visualizer("waveform") == VIZ_WAVEFORM
    assert normalize_visualizer("EQ") == VIZ_SPECTRUM
    assert normalize_visualizer("none") == VIZ_OFF
    assert normalize_visualizer("nope") == DEFAULT_VISUALIZER
    assert normalize_visualizer(None) == DEFAULT_VISUALIZER
    # Removed Loudness mode migrates to waveform.
    assert normalize_visualizer("rms") == VIZ_WAVEFORM
    assert normalize_visualizer("loudness") == VIZ_WAVEFORM


def test_pcm_and_animation_flags() -> None:
    assert visualizer_needs_pcm("spectrum") is True
    assert visualizer_needs_pcm("waveform") is True
    assert visualizer_needs_pcm("visualizer") is False
    assert visualizer_needs_pcm("off") is False
    assert visualizer_needs_pcm("text") is False
    assert visualizer_needs_pcm("cover") is False
    assert visualizer_animates("pulse") is True
    assert visualizer_animates("visualizer") is True
    assert visualizer_animates("off") is False
    assert visualizer_animates("text") is False


def test_locale_keys_cover_every_mode() -> None:
    from clipster import i18n

    for mode in VISUALIZER_MODES:
        key = visualizer_locale_key(mode)
        assert key == "discover_viz_{0}".format(mode)
    for language in ("en", "de"):
        messages = i18n.load(language)
        assert "discover_viz_rms" not in messages._data
        labels, label_to_mode, mode_to_label = visualizer_mode_choices(messages)
        assert len(labels) == 7
        assert labels[0] == messages[visualizer_locale_key(VIZ_OFF)]
        assert labels[1] == messages[visualizer_locale_key(VIZ_TEXT)]
        assert all(label.strip() for label in labels)
        assert set(label_to_mode.values()) == set(VISUALIZER_MODES)
        assert set(mode_to_label) == set(VISUALIZER_MODES)
        for mode in VISUALIZER_MODES:
            assert mode_to_label[mode] == messages[visualizer_locale_key(mode)]
            assert label_to_mode[mode_to_label[mode]] == mode


def test_downsample_waveform_and_loudness() -> None:
    samples = [0.0, 0.5, -0.5, 1.0, -1.0, 0.25]
    points = downsample_waveform(samples, 4)
    assert len(points) == 4
    assert all(-1.0 <= value <= 1.0 for value in points)
    assert downsample_waveform([], 8) == [0.0] * 8
    assert rms_of_samples([0.0, 0.0]) == 0.0
    assert 0.4 < rms_of_samples([0.5, -0.5, 0.5, -0.5]) < 0.6
    assert peak_of_samples([0.1, -0.9, 0.2]) == 0.9


def test_generative_waveform_is_alive_when_energy_positive() -> None:
    from clipster.visualizer import generative_waveform

    silent = generative_waveform(32, energy=0.0, phase=1.0)
    assert silent == [0.0] * 32
    live = generative_waveform(48, energy=0.7, phase=2.5)
    assert len(live) == 48
    assert max(abs(value) for value in live) > 0.1
    other = generative_waveform(48, energy=0.7, phase=3.1)
    assert live != other


def test_generative_mountain_is_dense_and_centred() -> None:
    silent = generative_mountain_levels(MOUNTAIN_BAR_COUNT, energy=0.0, phase=1.0)
    assert silent == [0.0] * MOUNTAIN_BAR_COUNT
    live = generative_mountain_levels(MOUNTAIN_BAR_COUNT, energy=0.8, phase=1.5)
    assert len(live) == MOUNTAIN_BAR_COUNT
    assert MOUNTAIN_BAR_COUNT > 10  # denser than classic EQ band count
    mid = MOUNTAIN_BAR_COUNT // 2
    # Centre-peaked silhouette (edges quieter than middle).
    assert live[mid] > live[0]
    assert live[mid] > live[-1]
    assert max(live) > 0.2
    other = generative_mountain_levels(MOUNTAIN_BAR_COUNT, energy=0.8, phase=2.7)
    assert live != other


def test_config_default_and_normalize(tmp_path: Path) -> None:
    assert Config().discover_visualizer == DEFAULT_VISUALIZER
    config = Config.from_dict({"discover_visualizer": "beat"}, tmp_path / "c.json")
    assert config.discover_visualizer == "pulse"
    config2 = Config.from_dict({"discover_visualizer": "garbage"}, tmp_path / "d.json")
    assert config2.discover_visualizer == DEFAULT_VISUALIZER
    config3 = Config.from_dict({"discover_visualizer": "rms"}, tmp_path / "e.json")
    assert config3.discover_visualizer == VIZ_WAVEFORM
