"""Why a download dies with HTTP 403 when nothing is actually blocked.

YouTube signs its media URLs with an ``n`` challenge that has to be solved in
JavaScript.  Without an engine yt-dlp accepts, every URL it hands out is either
refused at transfer time (403) or dropped from the format list before the
download starts - which reads like a block or a DRM wall and is neither.

These tests pin down the three things that went wrong in practice: an engine
that is present but too old was passed to yt-dlp anyway, no engine was provided
on the distributions that cannot supply one, and the resulting failure was
explained to the user as a 403 to be solved with cookies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipster import downloader, i18n, installer, paths


def _fake_exe(path: Path, output: str, exit_code: int = 0) -> Path:
    """Write an executable that prints ``output`` on any argument.

    :param path: Where to write the script.
    :param output: What it echoes.
    :param exit_code: What it exits with; QuickJS answers ``--help`` with 1.
    :return: ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\ncat <<'EOF'\n{0}\nEOF\nexit {1}\n".format(output, exit_code),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _no_system_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide every engine the machine really has, so a test decides alone."""
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    monkeypatch.setattr(installer, "_nvm_node_binaries", lambda: [])


# ----------------------------------------------------------------------
# QuickJS: present is not the same as usable
# ----------------------------------------------------------------------
def test_the_quickjs_debian_still_ships_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Debian and Ubuntu carry QuickJS 2021.03.27; yt-dlp wants 2023.12.09+.

    Handing it over regardless is worse than handing over nothing: the run looks
    configured, solving fails anyway, and the user is told about a 403.
    """
    qjs = _fake_exe(tmp_path / "qjs", "QuickJS version 2021-03-27", exit_code=1)
    _no_system_engines(monkeypatch)
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: str(qjs) if name == "qjs" else None
    )
    assert installer.find_js_runtime() is None


def test_a_recent_quickjs_is_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    qjs = _fake_exe(tmp_path / "qjs", "QuickJS version 2024-01-13", exit_code=1)
    _no_system_engines(monkeypatch)
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: str(qjs) if name == "qjs" else None
    )
    assert installer.find_js_runtime() == ("quickjs", str(qjs))


def test_quickjs_ng_is_accepted_at_any_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """yt-dlp versions the two flavours separately; ng has no minimum."""
    qjs = _fake_exe(tmp_path / "qjs", "QuickJS-ng version 0.10.1", exit_code=1)
    _no_system_engines(monkeypatch)
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: str(qjs) if name == "qjs" else None
    )
    assert installer.find_js_runtime() == ("quickjs-ng", str(qjs))


def test_an_old_deno_on_path_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    deno = _fake_exe(tmp_path / "deno", "deno 1.46.3 (release, x86_64)")
    _no_system_engines(monkeypatch)
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: str(deno) if name == "deno" else None
    )
    assert installer.find_js_runtime() is None


# ----------------------------------------------------------------------
# The private Deno Clipster installs for itself
# ----------------------------------------------------------------------
def test_the_private_deno_is_preferred_over_a_system_node(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ours is the one we know yt-dlp accepts; PATH can change behind our back."""
    node = _fake_exe(tmp_path / "node", "v22.21.1")
    _fake_exe(paths.bundled_deno_exe(), "deno 2.9.5 (stable, release, x86_64)")
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: str(node) if name in ("node", "nodejs") else None
    )
    monkeypatch.setattr(installer, "_nvm_node_binaries", lambda: [])
    assert installer.find_js_runtime() == ("deno", str(paths.bundled_deno_exe()))


def test_a_private_deno_that_is_too_old_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale download must not shadow a perfectly good system engine."""
    node = _fake_exe(tmp_path / "node", "v22.21.1")
    _fake_exe(paths.bundled_deno_exe(), "deno 1.40.0 (release, x86_64)")
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: str(node) if name in ("node", "nodejs") else None
    )
    monkeypatch.setattr(installer, "_nvm_node_binaries", lambda: [])
    assert installer.find_js_runtime() == ("node", str(node.resolve()))


def test_a_half_written_deno_is_not_reported_as_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that cannot be run is not an engine, however it got there."""
    target = paths.bundled_deno_exe()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not an executable")
    assert installer.bundled_deno() is None


# ----------------------------------------------------------------------
# Where the download comes from
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "machine,expected",
    [
        ("x86_64", "x86_64-unknown-linux-gnu"),
        ("aarch64", "aarch64-unknown-linux-gnu"),
        ("armv7l", None),
    ],
)
def test_the_deno_url_follows_the_architecture(
    monkeypatch: pytest.MonkeyPatch, machine: str, expected: str | None
) -> None:
    monkeypatch.setattr(paths, "is_termux", lambda: False)
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MACOS", False)
    monkeypatch.setattr(paths, "IS_LINUX", True)
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    url = installer.deno_download_url()
    if expected is None:
        assert url is None
    else:
        assert url is not None and expected in url


def test_android_gets_no_deno_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Termux has no Deno build and needs none - its nodejs package is current."""
    monkeypatch.setattr(paths, "is_termux", lambda: True)
    assert installer.deno_download_url() is None


def test_debian_is_not_asked_for_a_javascript_package() -> None:
    """Its only candidate is the QuickJS yt-dlp rejects.

    Declared unsupported rather than installed anyway, so nobody is asked for a
    sudo password to add a package that cannot solve the challenge.
    """
    apt = next(m for m in installer._PACKAGE_MANAGERS if m.name == "apt-get")
    assert apt.package_for("js") is None
    assert not apt.supports("js")


def test_termux_still_maps_a_javascript_package() -> None:
    """Its nodejs is new enough, and a shared package beats a private copy."""
    pkg = next(m for m in installer._PACKAGE_MANAGERS if m.name == "pkg")
    assert pkg.package_for("js") == "nodejs"


def test_a_failed_download_leaves_no_engine_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise ``bundled_deno`` would hand yt-dlp a broken file next time."""
    monkeypatch.setattr(installer, "_download", lambda _url, _target: "no route to host")
    monkeypatch.setattr(
        installer, "deno_download_url", lambda: "https://example.invalid/deno.zip"
    )
    assert installer.install_deno() == "no route to host"
    assert not paths.bundled_deno_exe().exists()


def test_the_setup_step_never_fails_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing engine is reported with a hint, not as a broken setup."""
    monkeypatch.setattr(installer, "find_js_runtime", lambda: None)
    monkeypatch.setattr(installer, "install_deno", lambda: "no Deno build for this platform")
    step = installer.ensure_js_runtime(auto_install=True)
    assert step.ok
    assert step.hint == installer.JS_RUNTIME_HINT


# ----------------------------------------------------------------------
# What the user is told
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        "ERROR: unable to download video data: HTTP Error 403: Forbidden",
        "ERROR: [youtube] IQU38vyzsIs: Requested format is not available",
        "ERROR: [youtube] IQU38vyzsIs: This video is DRM protected",
    ],
)
def test_a_missing_engine_is_named_instead_of_the_status_code(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    """403, "no formats" and a DRM report all mean this when no engine exists.

    Sending the user after cookies here costs them a login and changes nothing.
    """
    monkeypatch.setattr(downloader, "_js_runtime", lambda: None)
    messages = i18n.load("en")
    text = downloader.user_facing_ytdlp_error(message, messages)
    assert text == messages["error_no_js_runtime"]


def test_with_an_engine_the_403_keeps_its_own_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "_js_runtime", lambda: ("deno", "/usr/bin/deno"))
    messages = i18n.load("en")
    text = downloader.user_facing_ytdlp_error(
        "ERROR: unable to download video data: HTTP Error 403: Forbidden", messages
    )
    assert text == messages["error_forbidden"]


def test_an_empty_format_list_is_not_reported_as_a_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is a different problem: YouTube answered, it just offered nothing."""
    monkeypatch.setattr(downloader, "_js_runtime", lambda: ("deno", "/usr/bin/deno"))
    messages = i18n.load("en")
    text = downloader.user_facing_ytdlp_error(
        "ERROR: [youtube] IQU38vyzsIs: Requested format is not available", messages
    )
    assert text == messages["error_no_format"]
    assert text != messages["error_forbidden"]


def test_a_bot_check_still_wins_over_the_engine_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """"Sign in to confirm" is actionable and must not be masked."""
    monkeypatch.setattr(downloader, "_js_runtime", lambda: None)
    messages = i18n.load("en")
    text = downloader.user_facing_ytdlp_error(
        "HTTP Error 403: Forbidden. Sign in to confirm you're not a bot", messages
    )
    assert text == messages["error_bot_detected"]


def test_both_new_explanations_exist_in_every_language() -> None:
    for language in ("en", "de"):
        messages = i18n.load(language)
        for key in ("error_no_js_runtime", "error_no_format"):
            assert messages[key].strip(), "{0} missing in {1}".format(key, language)


# ----------------------------------------------------------------------
# The player client order
# ----------------------------------------------------------------------
def test_web_embedded_is_asked_before_android_vr() -> None:
    """When two clients offer one format, yt-dlp keeps the first client's URL.

    ``android_vr`` needs a GVS PO token Clipster does not collect, so leading
    with it hands out URLs that answer 403 and deduplicates the usable
    ``web_embedded`` one away - the exact failure this order prevents.
    """
    clients = downloader._PLAYER_CLIENTS
    assert clients.index("web_embedded") < clients.index("android_vr")


def test_no_first_attempt_client_needs_a_po_token() -> None:
    """``mweb`` and ``ios`` only skip their formats without one."""
    assert not {"mweb", "ios"}.intersection(downloader._PLAYER_CLIENTS)
