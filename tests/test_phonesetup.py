"""The guided phone setup.

The wizard is a console dialogue, so the tests drive it by feeding answers and
reading what it printed.  The pairing step is the interesting part: it starts a
real server and waits for a device, so one test plays the phone.
"""

from __future__ import annotations

import threading
import time
import urllib.request
from pathlib import Path
from typing import Iterator, List

import pytest

from clipster import phonesetup, webserver
from clipster.config import Config

#: The real implementations, kept before the autouse fixture hides them.
_real_pair = phonesetup._pair
_real_firewall_hint = phonesetup.firewall_hint


@pytest.fixture(autouse=True)
def quiet_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the steps that would ask machine-dependent questions or block.

    Whether a firewall question appears at all depends on what is installed on
    the machine running the tests, and the pairing step waits minutes for a real
    phone. Tests that are about those two steps override this again.
    """
    monkeypatch.setattr(phonesetup, "firewall_hint", lambda port: ("nothing to open", ""))
    monkeypatch.setattr(phonesetup, "_pair", lambda settings: "")


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    """Return a fresh configuration on a free port."""
    settings = Config.load(tmp_path / "config.json")
    settings.remote_port = 0
    settings.save()
    return settings


def _answers(monkeypatch: pytest.MonkeyPatch, replies: List[str]) -> Iterator:
    """Feed ``replies`` to every input() the wizard performs."""
    queue = list(replies)

    def fake_input(prompt: str = "") -> str:
        if not queue:
            raise EOFError("the wizard asked more than expected: " + prompt)
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return queue


# ----------------------------------------------------------------------
# Consent
# ----------------------------------------------------------------------
def test_saying_no_at_the_start_changes_nothing(config: Config, monkeypatch, capsys) -> None:
    _answers(monkeypatch, ["n"])
    assert phonesetup.run(config) == 1
    assert Config.load(config.path).remote_enabled is False
    assert "Nothing was changed" in capsys.readouterr().out


def test_saying_no_to_the_settings_changes_nothing(config: Config, monkeypatch, capsys) -> None:
    """The second question is the one that actually opens the network."""
    _answers(monkeypatch, ["y", "n"])
    assert phonesetup.run(config) == 1
    stored = Config.load(config.path)
    assert stored.remote_enabled is False
    assert stored.remote_bind == "127.0.0.1"
    assert not stored.remote_token


def test_the_plan_is_shown_before_anything_happens(config: Config, monkeypatch, capsys) -> None:
    _answers(monkeypatch, ["n"])
    phonesetup.run(config)
    output = capsys.readouterr().out
    assert "remote_enabled" in output
    assert "token" in output.lower()
    assert "not open anything to the internet" in output


def test_an_interrupted_question_aborts(config: Config, monkeypatch) -> None:
    def interrupt(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    assert phonesetup.run(config) == 1
    assert Config.load(config.path).remote_enabled is False


# ----------------------------------------------------------------------
# The settings it writes
# ----------------------------------------------------------------------
def test_it_writes_both_keys_and_a_token(config: Config, monkeypatch) -> None:
    _answers(monkeypatch, ["y", "y"])
    phonesetup.run(config)
    stored = Config.load(config.path)
    assert stored.remote_enabled is True
    assert stored.remote_bind == "0.0.0.0", "a phone cannot reach a loopback bind"
    assert len(stored.remote_token) >= 24


def test_an_existing_token_is_kept(config: Config, monkeypatch) -> None:
    """Re-running the wizard must not lock out a phone that is already paired."""
    config.remote_token = "keep-this-token-please-1234"
    config.save()
    _answers(monkeypatch, ["y", "y"])
    phonesetup.run(config)
    assert Config.load(config.path).remote_token == "keep-this-token-please-1234"


def test_an_already_configured_setup_asks_nothing_twice(config: Config, monkeypatch, capsys) -> None:
    config.remote_enabled = True
    config.remote_bind = "0.0.0.0"
    config.remote_token = "already-there-token-000000"
    config.save()
    _answers(monkeypatch, ["y"])
    phonesetup.run(config)
    assert "Already set up" in capsys.readouterr().out


# ----------------------------------------------------------------------
# The checks
# ----------------------------------------------------------------------
def test_a_free_port_is_recognised() -> None:
    assert phonesetup.port_is_free("127.0.0.1", 0) is True


def test_an_occupied_port_is_recognised(tmp_path: Path) -> None:
    server = webserver.RemoteServer(None, token="t", bind="127.0.0.1", port=0,
                                   web_root=tmp_path)
    assert server.start()
    try:
        assert phonesetup.port_is_free("127.0.0.1", server.port) is False
    finally:
        server.stop()


def test_the_firewall_hint_never_runs_anything(monkeypatch) -> None:
    """Looking must not change the system; only an agreed command may."""
    def explode(*args, **kwargs):
        raise AssertionError("the hint executed a command")

    monkeypatch.setattr(phonesetup.subprocess, "run", explode)
    monkeypatch.setattr(phonesetup.shutil, "which", lambda name: None)
    description, command = _real_firewall_hint(8733)
    assert description
    assert command == ""


def test_the_windows_hint_names_the_port(monkeypatch) -> None:
    monkeypatch.setattr(phonesetup.paths, "IS_WINDOWS", True)
    description, command = _real_firewall_hint(9001)
    assert "9001" in command
    assert "netsh" in command


def test_the_ufw_hint_names_the_port(monkeypatch) -> None:
    monkeypatch.setattr(phonesetup.paths, "IS_WINDOWS", False)
    monkeypatch.setattr(phonesetup.shutil, "which", lambda name: "/usr/sbin/ufw" if name == "ufw" else None)
    monkeypatch.setattr(phonesetup, "_ufw_is_active", lambda: True)
    description, command = _real_firewall_hint(9002)
    assert command == "sudo ufw allow 9002/tcp"


def test_an_inactive_ufw_needs_no_command(monkeypatch) -> None:
    monkeypatch.setattr(phonesetup.paths, "IS_WINDOWS", False)
    monkeypatch.setattr(phonesetup.shutil, "which", lambda name: "/usr/sbin/ufw" if name == "ufw" else None)
    monkeypatch.setattr(phonesetup, "_ufw_is_active", lambda: False)
    assert _real_firewall_hint(9003)[1] == ""


# ----------------------------------------------------------------------
# Pairing - the point of the whole wizard
# ----------------------------------------------------------------------
def test_the_pairing_page_reports_a_visit() -> None:
    api = phonesetup._PairingApi()
    assert not api.seen.is_set()
    status, payload = api.status()
    assert status == 200
    assert payload["pairing"] is True
    assert api.seen.is_set(), "the wizard has to notice the phone"


def test_nothing_can_be_downloaded_while_pairing() -> None:
    """The program itself is not running yet, so promising a download would lie."""
    api = phonesetup._PairingApi()
    assert api.submit("https://youtu.be/x", "mp3")[0] == 503
    assert api.delete("id")[0] == 404
    assert api.media("id") is None
    assert api.downloads() == (200, {"downloads": []})


def test_a_phone_that_connects_is_detected(config: Config, monkeypatch, capsys) -> None:
    """The whole promise of the wizard: it says so when the phone got through."""
    monkeypatch.setattr(phonesetup, "_pair", _real_pair)
    config.remote_enabled = True
    config.remote_token = "pairing-token-abcdefghijkl"
    config.remote_port = 0
    config.save()

    seen = {}
    original = phonesetup.RemoteServer

    class Recording(original):  # type: ignore[misc,valid-type]
        """Remembers the API and port so the test can play the phone."""

        def start(self) -> bool:
            started = super().start()
            if started:
                seen["port"] = self.port
                seen["api"] = self._api
            return started

    monkeypatch.setattr(phonesetup, "RemoteServer", Recording)
    monkeypatch.setattr(phonesetup, "PAIRING_TIMEOUT", 20)

    def act_like_a_phone() -> None:
        """Load the pairing page the way a phone would, once the server is up."""
        for _ in range(500):
            if "port" in seen:
                break
            time.sleep(0.02)
        else:  # pragma: no cover - the server always comes up in practice
            return
        base = "http://127.0.0.1:{0}".format(seen["port"])
        for route in ("/?token=" + config.remote_token, "/api/status"):
            request = urllib.request.Request(base + route)
            request.add_header(webserver.TOKEN_HEADER, config.remote_token)
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200

    # Started first: the wizard blocks waiting for exactly these requests.
    phone = threading.Thread(target=act_like_a_phone, daemon=True)
    phone.start()
    _answers(monkeypatch, ["y", "y"])
    assert phonesetup.run(config) == 0
    phone.join(timeout=5)

    output = capsys.readouterr().out
    assert "reached this PC" in output
    assert "Add to home screen" in output, "the remaining phone steps have to be explained"
    assert "Share" in output


def test_a_phone_that_never_connects_is_reported(config: Config, monkeypatch, capsys) -> None:
    monkeypatch.setattr(phonesetup, "_pair", _real_pair)
    config.remote_enabled = True
    config.remote_bind = "0.0.0.0"
    config.remote_token = "pairing-token-abcdefghijkl"
    config.remote_port = 0
    config.save()
    # A real, very short wait: patching Event.wait globally would break the
    # server's own shutdown, which waits on one too.
    monkeypatch.setattr(phonesetup, "PAIRING_TIMEOUT", 1)
    _answers(monkeypatch, ["y"])
    assert phonesetup.run(config) == 1
    output = capsys.readouterr().out
    assert "No phone got through" in output
    assert "different Wi-Fi" in output, "the likely reasons have to be named"


def test_the_pairing_page_is_not_the_real_interface() -> None:
    """It must not offer a download button that cannot work yet."""
    page = phonesetup._PAIRING_PAGE
    assert "Connected" in page
    assert "api/status" in page
    assert "api/submit" not in page


# ----------------------------------------------------------------------
# Reachability from both platforms
# ----------------------------------------------------------------------
def test_the_wizard_has_a_command_line_switch() -> None:
    from clipster import cli

    assert cli.build_parser().parse_args(["--phone-setup"]).phone_setup is True


def test_the_readme_documents_how_to_start_it() -> None:
    readme = Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "--phone-setup" in text
    assert "run.bat --phone-setup" in text, "Windows users need their own line"


def test_the_address_uses_the_port_that_was_really_bound(config: Config, monkeypatch, capsys) -> None:
    """With "remote_port": 0 the configured port is not the one in use.

    Building the QR code from the configured value would encode an address the
    phone cannot reach.
    """
    monkeypatch.setattr(phonesetup, "_pair", _real_pair)
    monkeypatch.setattr(phonesetup, "PAIRING_TIMEOUT", 1)
    config.remote_enabled = True
    config.remote_bind = "0.0.0.0"
    config.remote_token = "pairing-token-abcdefghijkl"
    config.remote_port = 0
    config.save()

    bound = {}
    original = phonesetup.RemoteServer

    class Recording(original):  # type: ignore[misc,valid-type]
        def start(self) -> bool:
            started = super().start()
            if started:
                bound["port"] = self.port
            return started

    monkeypatch.setattr(phonesetup, "RemoteServer", Recording)
    _answers(monkeypatch, ["y"])
    phonesetup.run(config)

    output = capsys.readouterr().out
    assert bound["port"] > 0
    assert ":{0}/".format(bound["port"]) in output
    assert ":0/" not in output, "the configured port 0 must never appear as an address"
