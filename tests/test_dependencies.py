"""The declarative dependency definition and what the installer makes of it."""

from __future__ import annotations

from pathlib import Path

import pytest

from clipster import dependencies, installer

PLATFORMS = ("linux", "windows", "macos")
ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_essentials_are_required_everywhere(platform: str) -> None:
    pips = dependencies.pip_dependencies(platform)
    systems = dependencies.system_dependencies(platform)
    assert any(p.package == "yt-dlp" and p.level == dependencies.LEVEL_REQUIRED for p in pips)
    assert any(s.name == "FFmpeg" and s.level == dependencies.LEVEL_REQUIRED for s in systems)
    assert any(s.name == "tkinter" and s.level == dependencies.LEVEL_REQUIRED for s in systems)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_entry_explains_itself(platform: str) -> None:
    """Without a reason the about page and the installer hints stay empty."""
    for item in dependencies.pip_dependencies(platform) + dependencies.system_dependencies(platform):
        assert item.feature, "{0} has no feature description".format(item)
        assert item.feature_key, "{0} has no message key".format(item)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_levels_are_one_of_the_two_known_values(platform: str) -> None:
    for item in dependencies.pip_dependencies(platform) + dependencies.system_dependencies(platform):
        assert item.level in (dependencies.LEVEL_REQUIRED, dependencies.LEVEL_OPTIONAL)


def test_linux_only_entries_stay_on_linux() -> None:
    linux_pips = [p.package for p in dependencies.pip_dependencies("linux")]
    windows_pips = [p.package for p in dependencies.pip_dependencies("windows")]
    assert "python-xlib" in linux_pips
    assert "python-xlib" not in windows_pips

    linux_systems = [s.name for s in dependencies.system_dependencies("linux")]
    windows_systems = [s.name for s in dependencies.system_dependencies("windows")]
    for name in ("Clipboard helper", "Tray menu support"):
        assert name in linux_systems
        assert name not in windows_systems


def test_only_yt_dlp_updates_itself() -> None:
    updating = [p.package for p in dependencies.PIP_DEPENDENCIES if p.auto_update]
    assert updating == ["yt-dlp"]


def test_lookups() -> None:
    assert dependencies.find("FFmpeg") is not None
    assert dependencies.find("does not exist") is None
    assert dependencies.find_pip("yt-dlp") is not None
    assert dependencies.find_pip("does not exist") is None


def test_the_current_platform_is_one_we_know() -> None:
    assert dependencies.current_platform() in PLATFORMS


def test_requirements_txt_is_generated_from_the_table() -> None:
    """The file is generated; an edit there would be lost on the next run."""
    assert dependencies.requirements_text() == (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_the_generated_requirements_mention_every_package() -> None:
    text = dependencies.requirements_text()
    for item in dependencies.PIP_DEPENDENCIES:
        assert item.package in text


def test_optional_helpers_agree_with_the_table() -> None:
    platform = dependencies.current_platform()
    assert dependencies.optional_pip_packages(platform)
    assert dependencies.optional_pip_modules(platform)


def test_every_optional_package_belongs_to_exactly_one_feature() -> None:
    """A package in no group is never installed; one in two groups is installed twice."""
    platform = dependencies.current_platform()
    groups = (dependencies.TRAY_FEATURE_KEYS, dependencies.QR_FEATURE_KEYS)
    collected: list = []
    for keys in groups:
        collected.extend(dependencies.optional_pip_packages(platform, keys))
    assert sorted(collected) == sorted(dependencies.optional_pip_packages(platform))
    assert len(collected) == len(set(collected))


def test_the_tray_does_not_claim_unrelated_packages() -> None:
    """A missing QR code library must not be reported as a broken system tray."""
    assert "qrcode" not in installer.tray_modules()
    assert installer.qr_modules() == ["qrcode"]


def test_the_minimum_python_is_shared_with_the_installer() -> None:
    assert dependencies.MINIMUM_PYTHON == installer.MIN_PYTHON == (3, 8)


# ----------------------------------------------------------------------
# The bridge into the installer's package manager tables
# ----------------------------------------------------------------------
@pytest.mark.parametrize("manager", [m for m in installer._PACKAGE_MANAGERS if m.name != "brew"],
                         ids=lambda m: m.name)
def test_every_system_key_resolves_to_a_package(manager) -> None:
    """A key without a mapping would make the installer silently do nothing.

    A platform may declare a component as ``unsupported`` - Android has no system
    tray at all - but only explicitly, so that a forgotten mapping still fails.
    """
    for entry in dependencies.SYSTEM_DEPENDENCIES:
        if not entry.system_key or not manager.supports(entry.system_key):
            continue
        assert manager.package_for(entry.system_key), \
            "{0} has no package for '{1}'".format(manager.name, entry.system_key)


@pytest.mark.parametrize("manager", [m for m in installer._PACKAGE_MANAGERS if m.name != "brew"],
                         ids=lambda m: m.name)
def test_what_a_platform_declares_missing_really_has_no_package(manager) -> None:
    """Otherwise "unsupported" would hide a mapping that does exist."""
    for key in manager.unsupported:
        assert manager.package_for(key) is None, \
            "{0} declares '{1}' unsupported but maps it anyway".format(manager.name, key)


def test_a_mapping_with_several_packages_is_split() -> None:
    """PyGObject needs the typelib as well; both must reach the command line."""
    manager = next(m for m in installer._PACKAGE_MANAGERS if m.name == "apt-get")
    assert installer._package_names(manager, ["appindicator"]) == [
        "python3-gi", "gir1.2-ayatanaappindicator3-0.1",
    ]


def test_ordinary_mappings_are_unaffected() -> None:
    manager = next(m for m in installer._PACKAGE_MANAGERS if m.name == "apt-get")
    assert installer._package_names(manager, ["ffmpeg"]) == ["ffmpeg"]
    assert installer._package_names(manager, ["unknown-key"]) == []


def test_duplicates_are_collapsed() -> None:
    manager = next(m for m in installer._PACKAGE_MANAGERS if m.name == "apt-get")
    assert installer._package_names(manager, ["ffmpeg", "ffmpeg"]) == ["ffmpeg"]


def test_the_system_key_lookup_falls_back() -> None:
    assert installer._system_key("FFmpeg", "unused") == "ffmpeg"
    assert installer._system_key("no such dependency", "fallback") == "fallback"


def test_optional_steps_never_report_a_failure() -> None:
    """A missing tray must not stop the program from starting."""
    import sys

    assert installer.ensure_tray_support(Path(sys.executable), auto_install=False).ok
    assert installer.ensure_tray_menu(Path(sys.executable), auto_install=False).ok


def test_the_environment_sees_the_system_packages_on_linux() -> None:
    """Without them PyGObject stays invisible and the tray icon loses its menu."""
    from clipster import paths

    options = installer._venv_options()
    if paths.IS_LINUX:
        assert options == ["--system-site-packages"]
    else:
        assert options == []
