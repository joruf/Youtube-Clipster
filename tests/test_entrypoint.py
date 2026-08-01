"""The bootstrap entry point and the two starter scripts.

``run.py`` runs on the *system* interpreter before anything is installed, so it
has to stay parseable on the oldest Python the project claims to support.  A
``SyntaxError`` there would replace the friendly "your Python is too old"
message with a traceback.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from clipster import cli, dependencies, paths

ROOT = Path(__file__).resolve().parent.parent
RUN_PY = ROOT / "run.py"
SOURCE = RUN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


# ----------------------------------------------------------------------
# run.py stays compatible with old interpreters
# ----------------------------------------------------------------------
def test_the_entry_point_exists_and_is_executable() -> None:
    assert RUN_PY.is_file()
    assert paths.bootstrap_script() == RUN_PY


def test_it_uses_no_annotations() -> None:
    functions = [n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)]
    assert not [n for n in ast.walk(TREE) if isinstance(n, ast.AnnAssign)]
    assert not any(argument.annotation for f in functions for argument in f.args.args)
    assert not any(f.returns for f in functions)


def test_it_uses_no_f_strings() -> None:
    assert not [n for n in ast.walk(TREE) if isinstance(n, ast.JoinedStr)]


def test_it_imports_nothing_beyond_the_standard_library_up_front() -> None:
    top_level = [n for n in TREE.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name.split(".")[0] for n in top_level for a in n.names}
    assert names <= {"os", "sys"}, "a heavier import would fail before the version check"


def test_the_version_is_checked_before_the_package_is_imported() -> None:
    assert SOURCE.index("version_info") < SOURCE.index("from clipster.cli import")


def test_it_agrees_with_the_manifest_about_the_minimum_version() -> None:
    assignments = {t.id: n.value for n in TREE.body if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)}
    assert "MIN_PYTHON" in assignments
    assert ast.literal_eval(assignments["MIN_PYTHON"]) == dependencies.MINIMUM_PYTHON


def test_a_windows_double_click_keeps_the_error_visible() -> None:
    assert 'os.name == "nt"' in SOURCE and "input(" in SOURCE


# ----------------------------------------------------------------------
# The starter scripts
# ----------------------------------------------------------------------
def test_both_starters_point_at_the_entry_point() -> None:
    assert 'ENTRY="$SCRIPT_DIR/run.py"' in (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'set "ENTRY=%~dp0run.py"' in (ROOT / "install.bat").read_text(encoding="utf-8")


def test_the_windows_starter_can_install_python_and_waits_on_errors() -> None:
    content = (ROOT / "install.bat").read_text(encoding="utf-8", errors="replace").lower()
    assert "winget" in content
    assert "pause" in content, "a double clicked window would close before the error is read"


def test_no_file_still_refers_to_the_previous_name() -> None:
    """The entry point was renamed; a stale reference would break the launcher."""
    for path in list(ROOT.glob("*.py")) + list(ROOT.glob("*.sh")) + list(ROOT.glob("*.bat")) \
            + list((ROOT / "clipster").rglob("*.py")):
        assert "youtube-clipster.py" not in path.read_text(encoding="utf-8", errors="replace"), path


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
def test_the_parser_knows_every_documented_switch() -> None:
    parser = cli.build_parser()
    arguments = parser.parse_args([])
    for name in ("check", "skip_checks", "update", "reinstall", "no_venv", "no_auto_install",
                 "create_shortcut", "autostart", "config", "lang", "download_dir",
                 "no_window", "no_tray", "show_window", "verbose"):
        assert hasattr(arguments, name), name


def test_switches_are_parsed() -> None:
    arguments = cli.build_parser().parse_args(["--no-tray", "--show-window", "--lang", "de", "-v"])
    assert arguments.no_tray and arguments.show_window
    assert arguments.lang == "de" and arguments.verbose


def test_the_autostart_switch_only_accepts_on_and_off() -> None:
    assert cli.build_parser().parse_args(["--autostart", "on"]).autostart == "on"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--autostart", "maybe"])


def test_the_program_name_matches_the_file() -> None:
    assert cli.build_parser().prog == "run.py"


# ----------------------------------------------------------------------
# The relaunch into the environment
# ----------------------------------------------------------------------
def test_a_console_less_parent_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Started from the shortcut the relaunch must not open a console window."""
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr(cli.sys, "executable", r"C:\Python312\pythonw.exe")
    assert cli._started_without_console() is True
    monkeypatch.setattr(cli.sys, "executable", r"C:\Python312\python.exe")
    assert cli._started_without_console() is False


def test_it_is_never_true_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(cli.sys, "executable", "/usr/bin/pythonw")
    assert cli._started_without_console() is False
