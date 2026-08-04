"""The Phone page: the whole setup inside the program.

The page itself needs a display, so those tests are marked ``gui``.  The QR
drawing and the application side are checked without one.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from clipster import qrview
from clipster.config import Config


# ----------------------------------------------------------------------
# Drawing the code
# ----------------------------------------------------------------------
def test_a_matrix_is_produced_for_an_address() -> None:
    pytest.importorskip("qrcode")
    matrix = qrview.qr_matrix("http://192.168.1.42:8733/?token=abc")
    assert matrix is not None
    assert len(matrix) >= 21, "the smallest QR code is 21 modules wide"
    assert all(len(row) == len(matrix) for row in matrix), "a QR code is square"
    assert any(any(row) for row in matrix)


def test_an_empty_address_yields_no_matrix() -> None:
    assert qrview.qr_matrix("") is None


def test_a_missing_package_is_survived(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page has to keep working; it then shows the address as text."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "qrcode":
            raise ImportError("no qrcode here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert qrview.qr_matrix("http://example.com") is None


@pytest.mark.gui
def test_the_code_is_drawn_as_rectangles() -> None:
    pytest.importorskip("qrcode")
    root = tk.Tk()
    try:
        canvas = tk.Canvas(root, width=200, height=200)
        assert qrview.draw_qr(canvas, "http://192.168.1.42:8733/?token=abc", 200) is True
        items = canvas.find_all()
        assert len(items) > 10, "the modules have to end up on the canvas"
        # One background plus the module runs; no image, so no Pillow needed.
        assert all(canvas.type(item) == "rectangle" for item in items)
    finally:
        root.destroy()


@pytest.mark.gui
def test_drawing_twice_replaces_the_first_code() -> None:
    pytest.importorskip("qrcode")
    root = tk.Tk()
    try:
        canvas = tk.Canvas(root)
        qrview.draw_qr(canvas, "http://one.example/?token=a", 200)
        first = len(canvas.find_all())
        qrview.draw_qr(canvas, "http://two.example/?token=b", 200)
        assert len(canvas.find_all()) != 0
        assert first > 0
        # A stale code underneath would make the new one unscannable.
        assert canvas.find_all()[0] == min(canvas.find_all())
    finally:
        root.destroy()


@pytest.mark.gui
def test_a_canvas_without_a_code_is_left_empty() -> None:
    root = tk.Tk()
    try:
        canvas = tk.Canvas(root)
        canvas.create_rectangle(0, 0, 10, 10)
        assert qrview.draw_qr(canvas, "", 200) is False
        assert canvas.find_all() == (), "the old drawing has to go"
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# The page
# ----------------------------------------------------------------------
@pytest.fixture()
def page(tmp_path: Path):
    """Return a Phone page wired to recording callbacks."""
    pytest.importorskip("qrcode")
    from clipster import i18n, theme
    from clipster.phone_page import PhonePage

    config = Config.load(tmp_path / "config.json")
    root = tk.Tk()
    theme.apply(root)

    calls: Dict[str, List[Any]] = {"apply": [], "token": [], "copy": []}
    state: Dict[str, Any] = {
        "enabled": False, "bind": "127.0.0.1", "port": 8733, "token": "",
        "running": False, "url": "", "contacts": 0, "last_contact": "",
    }

    def apply(enabled: bool, bind: str, port: int) -> Dict[str, Any]:
        calls["apply"].append((enabled, bind, port))
        state.update(enabled=enabled, bind=bind, port=port)
        return state

    def new_token() -> Dict[str, Any]:
        calls["token"].append(True)
        state["token"] = "a-brand-new-token-value00"
        return state

    widget = PhonePage(
        root, messages=i18n.load("en"), palette=theme.PALETTE, config=config,
        fonts=theme.fonts(), on_apply=apply, on_new_token=new_token,
        on_state=lambda: state, on_copy=lambda text: calls["copy"].append(text),
        on_firewall_hint=lambda port: ("ufw is active.", "sudo ufw allow {0}/tcp".format(port)),
    )
    widget.pack(fill="both", expand=True)
    root.update_idletasks()
    try:
        yield widget, state, calls
    finally:
        widget.stop_polling()
        root.destroy()


def _labels(widget: tk.Misc) -> List[str]:
    """Collect the text of every label below ``widget``."""
    found = []
    for child in widget.winfo_children():
        try:
            text = str(child.cget("text"))
        except Exception:
            text = ""
        if text:
            found.append(text)
        found.extend(_labels(child))
    return found


pytestmark_gui = pytest.mark.gui


@pytest.mark.gui
def test_a_switched_off_interface_says_so(page) -> None:
    widget, state, _ = page
    widget.refresh()
    assert "off" in widget._status.cget("text").lower()


@pytest.mark.gui
def test_a_listening_interface_waits_for_a_phone(page) -> None:
    widget, state, _ = page
    state.update(enabled=True, running=True, url="http://192.168.1.42:8733/?token=t")
    widget.refresh()
    assert "waiting" in widget._status.cget("text").lower()


@pytest.mark.gui
def test_a_phone_that_was_there_is_reported(page) -> None:
    """The one thing the user actually wants to know."""
    widget, state, _ = page
    state.update(enabled=True, running=True, url="http://192.168.1.42:8733/?token=t",
                 contacts=3, last_contact="2026-08-04T18:41:07")
    widget.refresh()
    text = widget._status.cget("text")
    assert "2026-08-04 18:41:07" in text
    assert widget._status.cget("style") == "Panel.Success.TLabel"


@pytest.mark.gui
def test_a_port_that_could_not_be_bound_is_reported(page) -> None:
    widget, state, _ = page
    state.update(enabled=True, running=False)
    widget.refresh()
    assert widget._status.cget("style") == "Panel.Danger.TLabel"


@pytest.mark.gui
def test_the_token_is_masked_until_it_is_revealed(page) -> None:
    widget, state, _ = page
    state["token"] = "secret-token-value-000000"
    widget.refresh()
    assert state["token"] not in widget._token.cget("text")
    widget._toggle_token()
    assert widget._token.cget("text") == state["token"]
    widget._toggle_token()
    assert state["token"] not in widget._token.cget("text")


@pytest.mark.gui
def test_switching_it_on_reaches_the_application(page) -> None:
    widget, _, calls = page
    widget._enabled.set(True)
    widget._bind.set("0.0.0.0")
    widget._apply()
    assert calls["apply"][-1] == (True, "0.0.0.0", 8733)


@pytest.mark.gui
def test_an_unusable_port_falls_back_to_the_stored_one(page) -> None:
    """A typo must not silently move the interface to port 0."""
    widget, _, calls = page
    for nonsense in ("", "abc", "0", "-5", "99999"):
        widget._port.set(nonsense)
        widget._apply()
        assert calls["apply"][-1][2] == 8733, nonsense


@pytest.mark.gui
def test_a_valid_port_is_passed_on(page) -> None:
    widget, _, calls = page
    widget._port.set("9000")
    widget._apply()
    assert calls["apply"][-1][2] == 9000


@pytest.mark.gui
def test_a_new_token_is_confirmed_first(page, monkeypatch) -> None:
    """It disconnects every paired phone, so it must not happen by accident."""
    from tkinter import messagebox

    widget, _, calls = page
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    widget._new_token()
    assert calls["token"] == []

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    widget._new_token()
    assert calls["token"] == [True]


@pytest.mark.gui
def test_the_address_can_be_copied(page) -> None:
    widget, state, calls = page
    state.update(enabled=True, running=True, url="http://192.168.1.42:8733/?token=t")
    widget.refresh()
    widget._copy(widget._last_url)
    assert calls["copy"] == ["http://192.168.1.42:8733/?token=t"]


@pytest.mark.gui
def test_the_firewall_command_is_only_shown_when_it_matters(page) -> None:
    widget, state, _ = page
    state.update(enabled=True, running=True, bind="127.0.0.1")
    widget.refresh()
    assert widget._firewall.cget("text") == "", "nothing to open for a local bind"

    state.update(bind="0.0.0.0", port=8733)
    widget.refresh()
    assert "ufw" in widget._firewall.cget("text")
    assert widget._firewall_command.cget("text") == "sudo ufw allow 8733/tcp"


@pytest.mark.gui
def test_the_token_never_appears_in_the_shown_address(page) -> None:
    """It is on screen long enough for a shoulder to read it."""
    widget, state, _ = page
    state.update(enabled=True, running=True, url="http://192.168.1.42:8733/?token=sup3rsecret")
    widget.refresh()
    assert "sup3rsecret" not in widget._address.cget("text")
    assert widget._address.cget("text") == "http://192.168.1.42:8733/"


@pytest.mark.gui
def test_the_steps_for_the_phone_are_explained(page) -> None:
    widget, _, _ = page
    texts = " ".join(_labels(widget)).lower()
    assert "home screen" in texts
    assert "share" in texts


@pytest.mark.gui
def test_polling_stops_when_the_page_is_left(page) -> None:
    widget, _, _ = page
    widget.start_polling()
    assert widget._refresh_job is not None
    widget.stop_polling()
    assert widget._refresh_job is None
    widget.stop_polling()


@pytest.mark.gui
def test_a_broken_state_callback_does_not_take_the_window_down(page) -> None:
    widget, _, _ = page

    def explode() -> Dict[str, Any]:
        raise RuntimeError("no state today")

    widget._on_state = explode
    widget.refresh()


# ----------------------------------------------------------------------
# The application side
# ----------------------------------------------------------------------
@pytest.mark.gui
def test_the_state_describes_a_switched_off_interface(config, messages) -> None:
    from clipster.app import ClipsterApp

    app = ClipsterApp(config, messages)
    try:
        state = app.remote_state()
        assert state["enabled"] is False
        assert state["running"] is False
        assert state["url"] == ""
        assert state["contacts"] == 0
    finally:
        app._cancel_auto_discover_job()
        app.gui.destroy()


@pytest.mark.gui
def test_applying_settings_starts_and_stops_the_interface(config, messages) -> None:
    from clipster.app import ClipsterApp

    app = ClipsterApp(config, messages)
    try:
        state = app.apply_remote_settings(True, "127.0.0.1", 0)
        assert state["enabled"] is True
        assert state["running"] is True
        assert len(state["token"]) >= 24, "a token has to be generated"
        assert Config.load(config.path).remote_enabled is True

        state = app.apply_remote_settings(False, "127.0.0.1", 0)
        assert state["running"] is False
        assert Config.load(config.path).remote_enabled is False
    finally:
        app.stop_remote()
        app._cancel_auto_discover_job()
        app.gui.destroy()


@pytest.mark.gui
def test_a_new_token_replaces_the_old_one(config, messages) -> None:
    from clipster.app import ClipsterApp

    app = ClipsterApp(config, messages)
    try:
        first = app.apply_remote_settings(True, "127.0.0.1", 0)["token"]
        second = app.regenerate_remote_token()["token"]
        assert second != first
        assert app._remote is not None and app._remote.running, "it keeps serving"
        assert Config.load(config.path).remote_token == second
    finally:
        app.stop_remote()
        app._cancel_auto_discover_job()
        app.gui.destroy()


@pytest.mark.gui
def test_the_state_reports_a_contact_from_the_phone(config, messages) -> None:
    from clipster.app import ClipsterApp

    app = ClipsterApp(config, messages)
    try:
        app.apply_remote_settings(True, "127.0.0.1", 0)
        assert app.remote_state()["contacts"] == 0
        # What the server does when a phone asks for the list.
        app._remote._api.downloads()
        state = app.remote_state()
        assert state["contacts"] == 1
        assert state["last_contact"]
    finally:
        app.stop_remote()
        app._cancel_auto_discover_job()
        app.gui.destroy()


# ----------------------------------------------------------------------
# Reachable from the window
# ----------------------------------------------------------------------
@pytest.mark.gui
def test_the_page_is_in_the_menu(gui) -> None:
    view = gui.view
    assert "phone" in view._pages
    assert "phone" in view._menu_buttons
    view.select_page("phone")
    assert view.phone is not None
    assert view._page == "phone"


@pytest.mark.gui
def test_leaving_the_page_stops_its_polling(gui) -> None:
    view = gui.view
    view.select_page("phone")
    assert view.phone._refresh_job is not None
    view.select_page("downloads")
    assert view.phone._refresh_job is None


@pytest.mark.gui
def test_the_menu_label_is_translated(gui, messages) -> None:
    assert gui.view._menu_buttons["phone"].cget("text") == messages["page_phone"]


# ----------------------------------------------------------------------
# Naming: it is a remote control, not a phone feature
# ----------------------------------------------------------------------
#: Strings that describe the capability, so they must not narrow it to phones.
_DEVICE_NEUTRAL_KEYS = (
    "page_phone",
    "phone_enable",
    "phone_enable_hint",
    "phone_reach",
    "phone_reach_local",
    "phone_reach_network",
    "phone_status_off",
    "phone_status_waiting",
    "phone_status_contact",
    "phone_token_new_warning",
)

#: Words that would wrongly restrict those strings to a phone.
_PHONE_WORDS = ("phone", "handy", "iphone", "android", "tablet")


@pytest.mark.parametrize("key", _DEVICE_NEUTRAL_KEYS)
@pytest.mark.parametrize("language", ["en", "de"])
def test_the_capability_is_not_described_as_phone_only(key: str, language: str) -> None:
    """A tablet or a second computer can steer this just as well."""
    from clipster import i18n

    text = i18n.load(language)[key].lower()
    for word in _PHONE_WORDS:
        assert word not in text, "{0}/{1} says {2!r}".format(language, key, word)


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_steps_that_really_are_phone_specific_stay_so(language: str) -> None:
    """Generalising these away would make them useless.

    Scanning a code, the home screen and the share sheet exist on phones and
    tablets, and the wording has to keep naming the platforms.
    """
    from clipster import i18n

    messages = i18n.load(language)
    install = messages["phone_step_install"].lower()
    assert "android" in install and "iphone" in install
    assert "home" in install or "start" in install


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_tab_is_not_called_phone_any_more(language: str) -> None:
    from clipster import i18n

    label = i18n.load(language)["page_phone"]
    assert label
    assert label.lower() not in ("phone", "handy")
