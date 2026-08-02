"""Versioned terms-of-use helpers and application gate behaviour."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from clipster.config import Config
from clipster.terms import (
    TERMS_APP_VERSION,
    TERMS_STREAMING_VERSION,
    accept_app_terms,
    accept_streaming_terms,
    app_terms_accepted,
    streaming_terms_accepted,
    utc_now_iso,
)


@pytest.fixture()
def app(config, messages, monkeypatch):
    """Application with a real GUI; downloads are not exercised here."""
    from clipster.app import ClipsterApp

    instance = ClipsterApp(config, messages)
    monkeypatch.setattr(instance, "_handle_url", lambda *_args, **_kwargs: None)
    try:
        yield instance
    finally:
        instance.gui.destroy()


def test_utc_now_iso_is_timezone_aware() -> None:
    stamp = utc_now_iso()
    assert "T" in stamp
    assert stamp.endswith("+00:00") or stamp.endswith("Z")


def test_app_terms_are_not_accepted_by_default() -> None:
    config = Config()
    assert not app_terms_accepted(config)
    assert config.terms_app_version == ""
    assert config.terms_app_accepted_at == ""


def test_streaming_terms_are_not_accepted_by_default() -> None:
    config = Config()
    assert not streaming_terms_accepted(config)


def test_accept_app_terms_persists_version_and_timestamp(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    config = Config(path=target)
    accept_app_terms(config, when="2026-08-02T12:00:00+00:00")
    assert config.terms_app_version == TERMS_APP_VERSION
    assert config.terms_app_accepted_at == "2026-08-02T12:00:00+00:00"
    assert app_terms_accepted(config)
    again = Config.load(target)
    assert again.terms_app_version == TERMS_APP_VERSION
    assert again.terms_app_accepted_at == "2026-08-02T12:00:00+00:00"


def test_accept_streaming_terms_persists_version_and_timestamp(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    config = Config(path=target)
    accept_streaming_terms(config, when="2026-08-02T13:00:00+00:00")
    assert config.terms_streaming_version == TERMS_STREAMING_VERSION
    assert streaming_terms_accepted(config)
    again = Config.load(target)
    assert again.terms_streaming_version == TERMS_STREAMING_VERSION


def test_outdated_terms_version_requires_reacceptance() -> None:
    config = SimpleNamespace(
        terms_app_version="0",
        terms_app_accepted_at="2020-01-01T00:00:00+00:00",
        terms_streaming_version="0",
        terms_streaming_accepted_at="2020-01-01T00:00:00+00:00",
        save=lambda: None,
    )
    assert not app_terms_accepted(config)
    assert not streaming_terms_accepted(config)


def test_required_version_can_be_overridden() -> None:
    config = SimpleNamespace(
        terms_app_version="9",
        terms_streaming_version="9",
        terms_app_accepted_at="",
        terms_streaming_accepted_at="",
        save=lambda: None,
    )
    assert app_terms_accepted(config, required="9")
    assert streaming_terms_accepted(config, required="9")
    assert not app_terms_accepted(config, required="10")


@pytest.mark.gui
def test_post_start_quits_when_app_terms_are_declined(app, monkeypatch) -> None:
    app.config.terms_app_version = ""
    monkeypatch.setattr(app.gui, "ask_terms_acceptance", lambda **_kwargs: False)
    quit_calls: list[bool] = []
    monkeypatch.setattr(app, "request_quit", lambda: quit_calls.append(True))
    offered: list[bool] = []
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: offered.append(True))

    app._post_start()

    assert quit_calls == [True]
    assert offered == []
    assert not app_terms_accepted(app.config)


@pytest.mark.gui
def test_post_start_saves_app_terms_and_continues(app, monkeypatch) -> None:
    app.config.terms_app_version = ""
    monkeypatch.setattr(app.gui, "ask_terms_acceptance", lambda **_kwargs: True)
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: None)
    monkeypatch.setattr(app, "_sync_autostart", lambda: None)
    app.config.show_startup_notification = False
    app.config.check_updates = False

    app._post_start()

    assert app_terms_accepted(app.config)
    assert app.config.terms_app_accepted_at


@pytest.mark.gui
def test_post_start_skips_dialog_when_app_terms_already_accepted(app, monkeypatch) -> None:
    accept_app_terms(app.config, when="2026-01-01T00:00:00+00:00")
    dialogs: list[str] = []

    def record(**kwargs):
        dialogs.append(kwargs.get("title", ""))
        return True

    monkeypatch.setattr(app.gui, "ask_terms_acceptance", record)
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: None)
    monkeypatch.setattr(app, "_sync_autostart", lambda: None)
    app.config.show_startup_notification = False
    app.config.check_updates = False

    app._post_start()

    assert dialogs == []


@pytest.mark.gui
def test_discover_refresh_is_blocked_without_streaming_terms(app, monkeypatch) -> None:
    accept_app_terms(app.config)
    app.config.terms_streaming_version = ""
    monkeypatch.setattr(app.gui, "ask_terms_acceptance", lambda **_kwargs: False)
    seeds_called: list[bool] = []

    def no_seeds(*_args, **_kwargs):
        seeds_called.append(True)
        return [], "history"

    monkeypatch.setattr("clipster.app.resolve_discover_seeds", no_seeds)
    app._discover_refresh()

    assert not streaming_terms_accepted(app.config)
    assert seeds_called == []
    assert not app._discover_busy


@pytest.mark.gui
def test_discover_refresh_proceeds_after_streaming_terms_accepted(app, monkeypatch) -> None:
    accept_app_terms(app.config)
    app.config.terms_streaming_version = ""
    monkeypatch.setattr(app.gui, "ask_terms_acceptance", lambda **_kwargs: True)
    seeds_called: list[bool] = []

    def no_seeds(*_args, **_kwargs):
        seeds_called.append(True)
        return [], "history"

    monkeypatch.setattr("clipster.app.resolve_discover_seeds", no_seeds)
    app._discover_refresh()

    assert streaming_terms_accepted(app.config)
    assert seeds_called == [True]


@pytest.mark.gui
def test_streaming_terms_dialog_is_shown_only_once(app, monkeypatch) -> None:
    accept_app_terms(app.config)
    dialogs: list[str] = []

    def record(**kwargs):
        dialogs.append("ask")
        return True

    monkeypatch.setattr(app.gui, "ask_terms_acceptance", record)
    assert app._ensure_streaming_terms() is True
    assert app._ensure_streaming_terms() is True
    assert dialogs == ["ask"]


def _find_terms_dialog(root):
    """Return the open terms Toplevel, or ``None`` while it is still building."""
    import tkinter as tk

    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel) and hasattr(child, "_terms_text"):
            return child
    return None


@pytest.mark.gui
def test_terms_acceptance_language_toggle_updates_body(gui) -> None:
    """Switching EN/DE replaces the body and keeps the checkbox state."""
    from clipster import i18n

    en_body = i18n.load("en")["terms_app_body"]
    de_body = i18n.load("de")["terms_app_body"]
    config_language = gui.config.language
    seen: dict[str, object] = {}

    def interact(retries: int = 50) -> None:
        dialog = _find_terms_dialog(gui.root)
        if dialog is None:
            if retries <= 0:
                raise AssertionError("terms acceptance dialog did not open")
            gui.root.after(20, lambda: interact(retries - 1))
            return
        text = dialog._terms_text
        seen["initial"] = text.get("1.0", "end-1c")
        dialog._terms_agreed.set(True)
        dialog._terms_lang_var.set("de")
        dialog._terms_apply_language("de")
        seen["after"] = text.get("1.0", "end-1c")
        seen["agreed"] = dialog._terms_agreed.get()
        seen["config"] = gui.config.language
        dialog.destroy()

    gui.root.after(20, interact)
    accepted = gui.ask_terms_acceptance(
        title_key="terms_app_title",
        body_key="terms_app_body",
    )

    assert accepted is False
    assert seen["initial"] == en_body
    assert seen["after"] == de_body
    assert seen["agreed"] is True
    assert seen["config"] == config_language


@pytest.mark.gui
def test_show_terms_document_language_toggle_updates_body(gui) -> None:
    """About terms dialog can switch languages without touching config.language."""
    from clipster import i18n

    en = i18n.load("en")
    de = i18n.load("de")
    en_body = "{0}\n\n{1}".format(en["terms_app_body"], en["terms_streaming_body"])
    de_body = "{0}\n\n{1}".format(de["terms_app_body"], de["terms_streaming_body"])
    config_language = gui.config.language
    seen: dict[str, object] = {}

    def interact(retries: int = 50) -> None:
        dialog = _find_terms_dialog(gui.root)
        if dialog is None:
            if retries <= 0:
                raise AssertionError("terms document dialog did not open")
            gui.root.after(20, lambda: interact(retries - 1))
            return
        text = dialog._terms_text
        seen["initial"] = text.get("1.0", "end-1c")
        dialog._terms_lang_var.set("de")
        dialog._terms_apply_language("de")
        seen["after"] = text.get("1.0", "end-1c")
        seen["config"] = gui.config.language
        dialog.destroy()

    gui.root.after(20, interact)
    gui.show_terms_document(
        title_key="terms_app_title",
        body_keys=("terms_app_body", "terms_streaming_body"),
    )

    assert seen["initial"] == en_body
    assert seen["after"] == de_body
    assert seen["config"] == config_language


@pytest.mark.gui
def test_app_terms_gate_passes_message_keys(app, monkeypatch) -> None:
    app.config.terms_app_version = ""
    captured: dict[str, object] = {}

    def record(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(app.gui, "ask_terms_acceptance", record)
    monkeypatch.setattr(app, "_maybe_offer_desktop_shortcut", lambda: None)
    monkeypatch.setattr(app, "_sync_autostart", lambda: None)
    app.config.show_startup_notification = False
    app.config.check_updates = False

    app._post_start()

    assert captured["title_key"] == "terms_app_title"
    assert captured["body_key"] == "terms_app_body"
    assert app_terms_accepted(app.config)
