"""The helper that gets the phone interface onto a phone.

``tools/phone_link.py`` is what the README tells the user to run, so it has to
work on a configuration that has never been used before.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "phone_link.py"


@pytest.fixture()
def tool():
    """Import the tool without running it."""
    spec = importlib.util.spec_from_file_location("clipster_phone_link", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_readme_command_exists() -> None:
    assert SCRIPT.is_file()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "tools/phone_link.py" in readme, "the documented command must exist"


def test_it_prints_only_the_url_when_asked(tool, tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.json"
    assert tool.main(["--config", str(config), "--url"]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1, printed
    assert printed[0].startswith("http://")
    assert "token=" in printed[0]


def test_a_missing_token_is_generated_and_kept(tool, tmp_path: Path, capsys) -> None:
    """Otherwise the link could only be produced after the first program start."""
    from clipster.config import Config

    config = tmp_path / "config.json"
    assert tool.main(["--config", str(config), "--url"]) == 0
    first = capsys.readouterr().out.strip()
    stored = Config.load(config).remote_token
    assert len(stored) >= 24
    assert stored in first

    assert tool.main(["--config", str(config), "--url"]) == 0
    assert capsys.readouterr().out.strip() == first, "the token must not change"


def test_it_warns_while_the_interface_is_switched_off(tool, tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.json"
    assert tool.main(["--config", str(config)]) == 0
    output = capsys.readouterr().out
    assert "remote_enabled" in output
    assert "remote_bind" in output, "and that a phone cannot reach a loopback bind"


def test_it_draws_a_qr_code(tool, tmp_path: Path, capsys) -> None:
    pytest.importorskip("qrcode")
    assert tool.main(["--config", str(tmp_path / "config.json")]) == 0
    output = capsys.readouterr().out
    # print_ascii draws with half block characters.
    assert any(block in output for block in ("█", "▀", "▄")), output[:200]


def test_it_can_write_a_png(tool, tmp_path: Path) -> None:
    pytest.importorskip("qrcode")
    target = tmp_path / "shots" / "link.png"
    assert tool.main(["--config", str(tmp_path / "config.json"), "--png", str(target)]) == 0
    assert target.is_file()
    assert target.read_bytes().startswith(b"\x89PNG")


def test_the_qr_code_is_generated_locally(tool) -> None:
    """The token is a password - it must not be sent to a web service."""
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("urlopen", "requests", "http://api", "https://api", "chart.googleapis"):
        assert forbidden not in source, forbidden
