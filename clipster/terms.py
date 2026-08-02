"""Versioned terms-of-use acceptance for Clipster.

Stores which text revision the user accepted.  Bump
:data:`TERMS_APP_VERSION` / :data:`TERMS_STREAMING_VERSION` when the legal
copy changes so users must re-confirm.

This is not legal advice; it documents informed consent in the application.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

#: Bump when the general (app-wide) terms text changes.
TERMS_APP_VERSION = "2"
#: Bump when the Streaming-specific terms text changes.
TERMS_STREAMING_VERSION = "2"


class TermsConfig(Protocol):
    """Minimal config surface used by the acceptance helpers."""

    terms_app_version: str
    terms_app_accepted_at: str
    terms_streaming_version: str
    terms_streaming_accepted_at: str

    def save(self) -> None:
        """Persist the configuration."""


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def app_terms_accepted(config: TermsConfig, *, required: str = TERMS_APP_VERSION) -> bool:
    """Return ``True`` when ``config`` holds the current app terms version."""
    return str(config.terms_app_version or "").strip() == str(required).strip()


def streaming_terms_accepted(config: TermsConfig, *, required: str = TERMS_STREAMING_VERSION) -> bool:
    """Return ``True`` when ``config`` holds the current Streaming terms version."""
    return str(config.terms_streaming_version or "").strip() == str(required).strip()


def accept_app_terms(config: TermsConfig, *, version: str = TERMS_APP_VERSION, when: str | None = None) -> None:
    """Record acceptance of the app-wide terms and save the config."""
    config.terms_app_version = str(version).strip()
    config.terms_app_accepted_at = when or utc_now_iso()
    config.save()


def accept_streaming_terms(
    config: TermsConfig,
    *,
    version: str = TERMS_STREAMING_VERSION,
    when: str | None = None,
) -> None:
    """Record acceptance of the Streaming terms and save the config."""
    config.terms_streaming_version = str(version).strip()
    config.terms_streaming_accepted_at = when or utc_now_iso()
    config.save()
