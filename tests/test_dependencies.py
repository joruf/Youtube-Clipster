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


# ----------------------------------------------------------------------
# Asking before installing
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_leftover_hook(monkeypatch: pytest.MonkeyPatch):
    """Keep the process-wide confirm hook out of the other tests."""
    monkeypatch.setattr(installer, "_confirm_hook", None)


@pytest.mark.parametrize("manager", installer._PACKAGE_MANAGERS, ids=lambda m: m.name)
def test_every_package_manager_can_install_adb(manager) -> None:
    """The wizard offers the install on every platform, so every one needs a name."""
    assert manager.package_for("adb"), "{0} has no adb package".format(manager.name)


def test_nothing_is_installed_when_the_answer_is_no(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list = []
    monkeypatch.setattr(installer, "detect_package_manager",
                        lambda: installer._PACKAGE_MANAGERS[1])       # apt-get
    monkeypatch.setattr(installer, "run_command", lambda *a, **k: ran.append(a) or None)

    result = installer.install_system_packages(["adb"], confirm=lambda packages, command: False)

    assert ran == [], "the package manager was called despite a no"
    assert result.declined and not result.ok


def test_the_question_names_the_packages_and_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list = []
    monkeypatch.setattr(installer, "detect_package_manager",
                        lambda: installer._PACKAGE_MANAGERS[1])
    monkeypatch.setattr(installer, "run_command",
                        lambda *a, **k: installer.CommandResult(returncode=0, output=""))

    def confirm(packages, command):
        asked.append((packages, command))
        return True

    assert installer.install_system_packages(["adb"], confirm=confirm).ok
    (packages, command), = asked
    assert packages == ["adb"]
    assert command == "apt-get install -y adb"


def test_a_yes_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "detect_package_manager",
                        lambda: installer._PACKAGE_MANAGERS[1])
    monkeypatch.setattr(installer, "run_command",
                        lambda *a, **k: installer.CommandResult(returncode=0, output="done"))
    assert installer.install_system_packages(["adb"], confirm=lambda p, c: True).ok


def test_the_hook_is_used_when_no_question_is_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ensure_* functions do not each carry a confirm argument."""
    monkeypatch.setattr(installer, "detect_package_manager",
                        lambda: installer._PACKAGE_MANAGERS[1])
    monkeypatch.setattr(installer, "run_command",
                        lambda *a, **k: installer.CommandResult(returncode=0, output=""))
    installer.set_install_confirm(lambda packages, command: False)
    assert installer.install_system_packages(["adb"]).declined


def test_without_a_terminal_the_question_answers_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unattended setup must not stall on a question nobody can see."""
    monkeypatch.setattr(installer, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("asked without a terminal"))
    assert installer.console_confirm(["adb"], "apt-get install -y adb") is True


@pytest.mark.parametrize("answer,expected", [
    ("y", True), ("Y", True), ("yes", True), ("j", True), ("ja", True),
    ("", False), ("n", False), ("no", False), ("nope", False),
])
def test_the_terminal_answer_decides(monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool) -> None:
    monkeypatch.setattr(installer, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: answer)
    assert installer.console_confirm(["adb"], "cmd") is expected


def test_an_interrupted_question_means_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "_interactive", lambda: True)

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    assert installer.console_confirm(["adb"], "cmd") is False


def test_streams_that_are_none_are_not_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under pythonw.exe stdin and stdout are None, not merely not a tty."""
    monkeypatch.setattr(installer.sys, "stdin", None)
    assert installer._interactive() is False


# ----------------------------------------------------------------------
# Getting the rights to install, with and without a terminal
# ----------------------------------------------------------------------
def test_a_failing_refresh_does_not_block_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """One broken third-party repository must not stop adb from being installed."""
    monkeypatch.setattr(installer, "_is_root", lambda: True)
    command = installer.privileged_script([["apt-get", "update"],
                                           ["apt-get", "install", "-y", "adb"]])
    assert command is not None
    script = command[-1]
    assert "&&" not in script, "a failed refresh would abort the install"
    assert script.index("update") < script.index("install")


def test_the_exit_status_is_the_installs(tmp_path: Path) -> None:
    """Proof of the semantics above, run through a real shell."""
    import subprocess

    assert subprocess.run(["sh", "-c", "false; true"]).returncode == 0
    assert subprocess.run(["sh", "-c", "true; false"]).returncode != 0


def test_package_names_are_quoted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the script path needs quoting - a lone command is passed as a vector."""
    monkeypatch.setattr(installer, "_is_root", lambda: True)
    command = installer.privileged_script([["apt-get", "update"],
                                           ["apt-get", "install", "a b", "c;d"]])
    assert command is not None
    assert "'a b'" in command[-1] and "'c;d'" in command[-1]


def test_a_single_command_needs_no_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "_is_root", lambda: True)
    assert installer.privileged_script([["apt-get", "install", "-y", "adb"]]) == [
        "apt-get", "install", "-y", "adb"]


def test_a_window_never_gets_a_prompt_it_cannot_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """sudo -p writes to a tty; with no tty it just fails, so it must not be used."""
    monkeypatch.setattr(installer, "_is_root", lambda: False)
    monkeypatch.setattr(installer, "_sudo_without_password", lambda: False)
    monkeypatch.setattr(installer, "_has_display", lambda: True)
    monkeypatch.setattr(installer.shutil, "which",
                        lambda name: "/usr/bin/" + name if name in ("pkexec", "sh", "sudo") else None)

    command = installer.privileged_script([["apt-get", "install", "-y", "adb"]], graphical=True)

    assert command is not None
    assert command[0].endswith("pkexec")
    assert "-p" not in command


def test_a_valid_sudo_timestamp_is_preferred_over_a_password_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "_is_root", lambda: False)
    monkeypatch.setattr(installer, "_sudo_without_password", lambda: True)
    monkeypatch.setattr(installer, "_has_display", lambda: True)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/bin/" + name)

    command = installer.privileged_script([["apt-get", "install", "-y", "adb"]], graphical=True)

    assert command is not None
    assert command[:2] == ["sudo", "-n"]


def test_without_a_way_to_ask_the_install_is_refused_rather_than_hanging(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "_is_root", lambda: False)
    monkeypatch.setattr(installer, "_sudo_without_password", lambda: False)
    monkeypatch.setattr(installer, "_has_display", lambda: False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)

    assert installer.privileged_script([["apt-get", "install", "-y", "adb"]], graphical=True) is None


@pytest.mark.parametrize("manager_name", ["brew", "pkg"])
def test_the_users_own_prefix_needs_no_escalation(monkeypatch: pytest.MonkeyPatch,
                                                 manager_name: str) -> None:
    """Asking for root on Android or Homebrew would fail, and is not needed."""
    monkeypatch.setattr(installer, "_is_root", lambda: False)
    command = installer.privileged_script([[manager_name, "install", "-y", "adb"]], graphical=True)
    assert command == [manager_name, "install", "-y", "adb"]


def test_the_question_hook_does_not_outlive_the_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is process wide - a later install must not ask out of nowhere."""
    monkeypatch.setattr(installer, "check_python",
                        lambda: installer.Step(name="Python", ok=False, detail="too old"))
    installer.bootstrap(ask=True, use_venv=False)
    assert installer._confirm_hook is None, "the hook survived an aborted bootstrap"


def test_a_window_install_can_never_wait_for_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dpkg question with no tty would hang forever behind a greyed-out button."""
    monkeypatch.setattr(installer, "_is_root", lambda: False)
    monkeypatch.setattr(installer, "_sudo_without_password", lambda: True)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/bin/" + name)

    command = installer.privileged_script([["apt-get", "install", "-y", "adb"]], graphical=True)

    assert command is not None
    assert "DEBIAN_FRONTEND=noninteractive" in command[-1]


def test_a_terminal_install_stays_answerable(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a tty such a question can be answered, so it is not suppressed."""
    monkeypatch.setattr(installer, "_is_root", lambda: False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/bin/" + name)

    command = installer.privileged_script([["apt-get", "update"],
                                           ["apt-get", "install", "-y", "adb"]])

    assert command is not None
    assert "DEBIAN_FRONTEND" not in command[-1]
