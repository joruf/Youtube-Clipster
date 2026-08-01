"""Translations: completeness, placeholders and the fallback behaviour."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from clipster import i18n

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "clipster" / "locales"
SOURCES = " ".join(p.read_text(encoding="utf-8") for p in (ROOT / "clipster").rglob("*.py"))

#: Every key the code asks for, collected from ``messages[...]`` / ``.format(...)``.
USED_KEYS = set(re.findall(r"messages(?:\[|\.format\()[\"']([a-z0-9_]+)[\"']", SOURCES))


def _raw(language: str) -> dict:
    """Return the decoded locale file without any fallback applied."""
    return json.loads((LOCALES / "{0}.json".format(language)).read_text(encoding="utf-8"))


def test_at_least_english_and_german_exist() -> None:
    assert {"de", "en"} <= set(i18n.available_languages())


@pytest.mark.parametrize("language", sorted(i18n.available_languages()))
def test_every_locale_file_is_valid_json(language: str) -> None:
    assert isinstance(_raw(language), dict)


def test_the_languages_carry_exactly_the_same_keys() -> None:
    english, german = set(_raw("en")), set(_raw("de"))
    assert english - german == set(), "missing in de"
    assert german - english == set(), "missing in en"


def test_the_code_asks_for_nothing_that_is_missing() -> None:
    assert sorted(key for key in USED_KEYS if key not in _raw("en")) == []


def test_no_translation_is_left_over() -> None:
    """An unused key is dead weight and usually a leftover from a rewrite."""
    assert [key for key in _raw("en") if key not in SOURCES] == []


@pytest.mark.parametrize("language", ["en", "de"])
def test_no_translation_is_empty(language: str) -> None:
    for key, value in _raw(language).items():
        if isinstance(value, str):
            assert value.strip(), "{0} is empty in {1}".format(key, language)


def test_both_languages_use_the_same_placeholders() -> None:
    """A placeholder that only exists in one language raises at runtime."""
    english, german = _raw("en"), _raw("de")
    for key, value in english.items():
        if not isinstance(value, str):
            continue
        assert set(re.findall(r"{(\w+)}", value)) == set(re.findall(r"{(\w+)}", german[key])), key


@pytest.mark.parametrize(
    "key,placeholder",
    [
        ("status_done", "title"), ("history_problem", "details"), ("progress_converting", "format"),
        ("lang_original", "language"), ("error_generic", "details"), ("error_metadata", "details"),
        ("error_disk_full", "details"), ("shortcut_created", "path"), ("only_one_instance_pid", "pid"),
    ],
)
def test_known_placeholders_are_filled(key: str, placeholder: str) -> None:
    rendered = i18n.load("en").format(key, **{placeholder: "VALUE"})
    assert "VALUE" in rendered
    assert "{" not in rendered


def test_an_unknown_language_falls_back_to_english() -> None:
    assert i18n.load("xx").language == "en"
    assert i18n.load(None).language == "en"


def test_an_unknown_key_returns_the_key_itself() -> None:
    """Better a visible key than an exception in the middle of the interface."""
    assert i18n.load("en")["no_such_key"] == "no_such_key"


def test_language_labels_are_translated() -> None:
    assert i18n.load("de").language_label("de") == "Deutsch (de)"
    assert i18n.load("en").language_label("de") == "German (de)"


def test_an_unknown_language_code_is_shown_as_is() -> None:
    assert "xy" in i18n.load("en").language_label("xy")


def test_get_returns_the_given_default() -> None:
    assert i18n.load("en").get("no_such_key", "fallback") == "fallback"


def test_every_dependency_description_is_translated() -> None:
    from clipster import dependencies

    english = _raw("en")
    for item in dependencies.PIP_DEPENDENCIES + dependencies.SYSTEM_DEPENDENCIES:
        assert item.feature_key in english, item.feature_key
