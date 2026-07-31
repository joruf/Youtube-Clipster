"""Cross-platform contract tests for YouTube Clipster (no GUI required)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clipster import dependencies, paths
from clipster.cli import build_parser
from clipster.dependencies import LEVEL_REQUIRED


class TestPathsContract(unittest.TestCase):
    """Filesystem layout must stay consistent across platforms."""

    def test_home_override_relocates_install_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {paths.HOME_ENV_VAR: tmp}):
                self.assertEqual(paths.install_dir(), Path(tmp))
                self.assertEqual(paths.venv_dir(), Path(tmp) / "venv")

    def test_project_root_contains_run_py(self) -> None:
        self.assertTrue((paths.PROJECT_ROOT / "run.py").is_file())
        self.assertTrue((paths.PROJECT_ROOT / "clipster").is_dir())

    def test_venv_python_name_matches_platform(self) -> None:
        name = paths.venv_python().name
        if paths.IS_WINDOWS:
            self.assertEqual(name, "python.exe")
            self.assertEqual(paths.venv_python(gui=True).name, "pythonw.exe")
        else:
            self.assertEqual(name, "python")


class TestDependenciesContract(unittest.TestCase):
    """Dependency table drives installers and requirements.txt."""

    def test_ytdlp_is_required_everywhere(self) -> None:
        required = [
            dep
            for dep in dependencies.PIP_DEPENDENCIES
            if dep.package == "yt-dlp" and dep.level == LEVEL_REQUIRED
        ]
        self.assertTrue(required)
        for platform in ("linux", "windows", "macos"):
            self.assertTrue(any(dep.applies_to(platform) for dep in required))

    def test_requirements_text_lists_ytdlp(self) -> None:
        text = dependencies.requirements_text()
        self.assertIn("yt-dlp", text)

    def test_python_xlib_is_linux_only(self) -> None:
        xlib = [dep for dep in dependencies.PIP_DEPENDENCIES if dep.package == "python-xlib"]
        self.assertTrue(xlib)
        self.assertTrue(xlib[0].applies_to("linux"))
        self.assertFalse(xlib[0].applies_to("windows"))


class TestCliContract(unittest.TestCase):
    """Bootstrap CLI flags used by installers and matrix runs."""

    def test_check_flag_parses(self) -> None:
        ns = build_parser().parse_args(["--check", "--no-auto-install"])
        self.assertTrue(ns.check)
        self.assertTrue(ns.no_auto_install)

    def test_help_mentions_clipster(self) -> None:
        help_text = build_parser().format_help()
        self.assertIn("YouTube", help_text)


class TestModuleImports(unittest.TestCase):
    """Core modules import without a display server."""

    def test_import_core_modules(self) -> None:
        import clipster.config  # noqa: F401
        import clipster.history  # noqa: F401
        import clipster.i18n  # noqa: F401
        import clipster.logging_setup  # noqa: F401
        import clipster.singleinstance  # noqa: F401


if __name__ == "__main__":
    unittest.main()
