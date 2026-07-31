"""Loading of the JSON translation files in ``clipster/locales``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "en"


class Messages:
    """Read-only access to the translation strings of one language."""

    def __init__(self, data: Dict[str, Any], language: str) -> None:
        """
        :param data: The merged translation table.
        :param language: The resolved two-letter language code.
        """
        self._data = data
        self.language = language

    def __getitem__(self, key: str) -> str:
        """Return a translation, falling back to the key itself."""
        value = self._data.get(key, key)
        return value if isinstance(value, str) else key

    def get(self, key: str, default: str | None = None) -> str:
        """Return a translation or ``default`` when the key is unknown.

        :param key: The translation key.
        :param default: Value returned for unknown keys; defaults to the key.
        :return: The translated string.
        """
        value = self._data.get(key)
        if isinstance(value, str):
            return value
        return key if default is None else default

    def format(self, key: str, **kwargs: object) -> str:
        """Return a translation with ``str.format`` placeholders applied.

        :param key: The translation key.
        :param kwargs: Placeholder values.
        :return: The formatted string; unknown placeholders are left untouched.
        """
        template = self[key]
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template

    def language_label(self, code: str) -> str:
        """Return a human readable label for an audio track language code.

        :param code: An ISO language code such as ``de`` or ``en-US``.
        :return: For example ``German (de)`` - or just the code when unknown.
        """
        names = self._data.get("language_names")
        if isinstance(names, dict):
            name = names.get(code) or names.get(code.split("-")[0].lower())
            if isinstance(name, str):
                return "{0} ({1})".format(name, code)
        return code


def available_languages() -> List[str]:
    """Return the language codes shipped in ``clipster/locales``."""
    if not LOCALES_DIR.is_dir():
        return [DEFAULT_LANGUAGE]
    return sorted(path.stem for path in LOCALES_DIR.glob("*.json"))


def _read(language: str) -> Dict[str, Any]:
    """Read one locale file; return an empty table when it does not exist."""
    path = LOCALES_DIR / "{0}.json".format(language.lower())
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(language: str | None) -> Messages:
    """Load the translation table for ``language``.

    English is always loaded first so that partial translations silently fall
    back instead of showing raw keys.

    :param language: Requested language code, ``None`` selects the default.
    :return: The merged :class:`Messages` instance.
    """
    requested = (language or DEFAULT_LANGUAGE).lower()
    data = _read(DEFAULT_LANGUAGE)
    resolved = DEFAULT_LANGUAGE
    if requested != DEFAULT_LANGUAGE:
        extra = _read(requested)
        if extra:
            data.update(extra)
            resolved = requested
    return Messages(data, resolved)
