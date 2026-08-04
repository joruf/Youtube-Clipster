"""The phone interface: token, routing, Range requests, path safety.

A real ``ThreadingHTTPServer`` is started on a free port and talked to over
loopback.  The application is faked: what is under test here is the transport,
not the download pipeline.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from clipster import webserver
from clipster.history import STATUS_FAILED, STATUS_OK, HistoryEntry
from clipster.webapi import RemoteApi, entry_to_dict

TOKEN = "s3cret-token"


class FakeApp:
    """Just enough application for the API to work against."""

    def __init__(self, entries: Optional[List[HistoryEntry]] = None) -> None:
        self.entries = entries or []
        self.submitted: List[Tuple[str, str, bool]] = []
        self.deleted: List[HistoryEntry] = []
        self.result: Any = None
        self.raise_on_submit = False
        self.delete_result = True
        self.commands: List[Tuple[str, int, float]] = []
        self.searches: List[str] = []
        self.enqueued: List[Tuple[str, str, bool]] = []
        self.audio_url = ""
        self.terms_missing = False
        self.history = self  # the API reaches the history through the app

    # -- history -------------------------------------------------------
    def find_by_id(self, identifier: str) -> Optional[HistoryEntry]:
        for entry in self.entries:
            if entry.identifier() == identifier:
                return entry
        return None

    # -- application ---------------------------------------------------
    def submit_remote(self, url: str, media_format: str, force: bool = False):
        if self.raise_on_submit:
            raise RuntimeError("GUI bridge is not running")
        self.submitted.append((url, media_format, force))
        return self.result

    def delete_remote(self, entry: HistoryEntry) -> bool:
        self.deleted.append(entry)
        return self.delete_result

    def remote_status(self) -> Dict[str, Any]:
        return {"active": [{"url": "u", "percent": 42.0}], "queued": 1, "parallel": 1}

    # -- streaming -----------------------------------------------------
    def discover_remote_state(self) -> Dict[str, Any]:
        return {"available": True, "terms_accepted": True, "tracks": [
            {"index": 0, "video_id": "v1", "title": "Song One", "uploader": "Artist",
             "duration": 214, "seed_title": ""}],
            "index": 0, "playing": True, "position": 12.0, "duration": 214.0,
            "can_seek": True, "busy": False, "extending": False, "mode": "related", "level": 0.4,
            "volume": 42, "volume_controllable": True, "search_delay_ms": 1500,
            "search_results": 12}

    def discover_remote_search(self, query: str) -> Dict[str, Any]:
        self.searches.append(query)
        if self.terms_missing:
            return {"ok": False, "error": "terms_required", "results": []}
        return {"ok": True, "error": "", "results": [
            {"video_id": "bbbbbbbbbbb", "title": "Hit for " + query,
             "uploader": "Finder", "duration": 321,
             "url": "https://youtu.be/bbbbbbbbbbb"}]}

    def discover_remote_enqueue(self, video_id: str, title: str = "", uploader: str = "",
                                duration: int = 0, play: bool = True) -> Dict[str, Any]:
        self.enqueued.append((video_id, title, play))
        if len(video_id) != 11:
            return {"ok": False, "error": "unknown_track", "state": {}}
        return {"ok": True, "error": "", "state": self.discover_remote_state()}

    def discover_remote_audio(self, video_id: str) -> Tuple[str, Dict[str, str]]:
        # The headers travel with the URL: YouTube hands them out per format.
        if video_id == "aaaaaaaaaaa" and self.audio_url:
            return self.audio_url, {"User-Agent": "test"}
        return "", {}

    def discover_remote_command(self, command: str, index: int = -1,
                                seconds: float = 0.0) -> Dict[str, Any]:
        if self.raise_on_submit:
            raise RuntimeError("GUI bridge is not running")
        self.commands.append((command, index, seconds))
        if command == "nope":
            return {"ok": False, "error": "unknown_command", "state": {}}
        if command == "refresh" and self.terms_missing:
            return {"ok": False, "error": "terms_required", "state": {}}
        return {"ok": True, "error": "", "state": self.discover_remote_state()}


@pytest.fixture()
def web_dir(tmp_path: Path) -> Path:
    """Return a small static directory to serve."""
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<h1>Clipster</h1>", encoding="utf-8")
    (root / "app.js").write_text("// script", encoding="utf-8")
    (root / "assets" / "note.txt").write_text("deep", encoding="utf-8")
    return root


@pytest.fixture()
def served(web_dir: Path):
    """Start a real server on a free port and yield ``(app, base_url)``."""
    app = FakeApp()
    server = webserver.RemoteServer(RemoteApi(app), token=TOKEN, bind="127.0.0.1",
                                    port=0, web_root=web_dir)
    assert server.start(), "the test server did not come up"
    try:
        yield app, "http://127.0.0.1:{0}".format(server.port), server
    finally:
        server.stop()


def _request(url: str, *, method: str = "GET", token: Optional[str] = TOKEN,
             body: Optional[dict] = None, headers: Optional[Dict[str, str]] = None):
    """Perform one request and return ``(status, headers, body bytes)``."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        request.add_header(webserver.TOKEN_HEADER, token)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


# ----------------------------------------------------------------------
# The token
# ----------------------------------------------------------------------
def test_without_a_token_nothing_is_served(served) -> None:
    _, base, _ = served
    for path in ("/", "/api/downloads", "/api/status", "/media/anything"):
        status, _, _ = _request(base + path, token=None)
        assert status == 401, path


def test_a_wrong_token_is_refused(served) -> None:
    _, base, _ = served
    status, _, _ = _request(base + "/api/downloads", token="not-it")
    assert status == 401


def test_a_prefix_of_the_token_is_not_enough(served) -> None:
    _, base, _ = served
    status, _, _ = _request(base + "/api/downloads", token=TOKEN[:-1])
    assert status == 401


def test_the_token_may_come_from_the_url(served) -> None:
    """The first visit arrives from a QR code, which cannot set a header."""
    _, base, _ = served
    status, headers, _ = _request(base + "/?token=" + TOKEN, token=None)
    assert status == 200
    assert webserver.TOKEN_COOKIE in headers.get("Set-Cookie", "")


def test_the_cookie_alone_works_afterwards(served) -> None:
    """A media element cannot send a header, so the cookie has to carry it."""
    _, base, _ = served
    status, _, _ = _request(base + "/api/downloads", token=None,
                            headers={"Cookie": "{0}={1}".format(webserver.TOKEN_COOKIE, TOKEN)})
    assert status == 200


def test_the_cookie_is_restricted_to_this_site(served) -> None:
    _, base, _ = served
    _, headers, _ = _request(base + "/?token=" + TOKEN, token=None)
    cookie = headers.get("Set-Cookie", "")
    assert "SameSite=Strict" in cookie
    assert "Path=/" in cookie


def test_a_server_without_a_token_does_not_start(web_dir: Path) -> None:
    server = webserver.RemoteServer(RemoteApi(FakeApp()), token="", port=0, web_root=web_dir)
    assert server.start() is False
    assert not server.running


def test_a_fresh_token_is_long_and_unique() -> None:
    first, second = webserver.new_token(), webserver.new_token()
    assert first != second
    assert len(first) >= 24


# ----------------------------------------------------------------------
# Static files
# ----------------------------------------------------------------------
def test_the_interface_is_served_at_the_root(served) -> None:
    _, base, _ = served
    status, headers, body = _request(base + "/")
    assert status == 200
    assert b"Clipster" in body
    assert headers["Content-Type"].startswith("text/html")
    assert headers.get("Cache-Control") == "no-store"


def test_a_nested_asset_is_served(served) -> None:
    _, base, _ = served
    status, _, body = _request(base + "/assets/note.txt")
    assert status == 200
    assert body == b"deep"


@pytest.mark.parametrize("attack", [
    "/../config.json",
    "/..%2Fconfig.json",
    "/%2e%2e/%2e%2e/etc/passwd",
    "/assets/../../config.json",
    "//etc/passwd",
])
def test_no_request_can_reach_a_file_outside_the_web_directory(served, attack: str) -> None:
    """The table is built at startup; a path from the request is never used."""
    _, base, _ = served
    status, _, body = _request(base + attack)
    assert status in (400, 404), body


def test_an_unknown_path_is_a_clean_404(served) -> None:
    _, base, _ = served
    status, _, _ = _request(base + "/nope.html")
    assert status == 404


def test_the_file_table_only_contains_files(web_dir: Path) -> None:
    table = webserver.static_files(web_dir)
    assert set(table) == {"/index.html", "/app.js", "/assets/note.txt"}
    assert all(path.is_file() for path in table.values())


def test_a_missing_web_directory_is_survived(tmp_path: Path) -> None:
    assert webserver.static_files(tmp_path / "nothing") == {}


# ----------------------------------------------------------------------
# Reading the list
# ----------------------------------------------------------------------
def test_the_download_list_is_served_as_json(served, tmp_path: Path) -> None:
    app, base, _ = served
    target = tmp_path / "song.mp3"
    target.write_bytes(b"abc")
    app.entries.append(HistoryEntry(name="song.mp3", path=str(target), title="A song",
                                    url="u", media_format="mp3", size=3, status=STATUS_OK))
    status, headers, body = _request(base + "/api/downloads")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["downloads"][0]["name"] == "song.mp3"
    assert payload["downloads"][0]["playable"] is True


def test_the_path_on_the_pc_is_never_disclosed(tmp_path: Path) -> None:
    """The phone gets an id; where the file lives is none of its business."""
    target = tmp_path / "song.mp3"
    target.write_bytes(b"abc")
    entry = HistoryEntry(name="song.mp3", path=str(target), url="u", media_format="mp3")
    described = entry_to_dict(entry)
    assert "path" not in described
    assert str(tmp_path) not in json.dumps(described)


def test_a_failed_download_reports_its_problem(served) -> None:
    app, base, _ = served
    app.entries.append(HistoryEntry(name="broken", url="u", media_format="mp3",
                                    status=STATUS_FAILED, error_kind="bot",
                                    error="Sign in to confirm"))
    payload = json.loads(_request(base + "/api/downloads")[2])
    entry = payload["downloads"][0]
    assert entry["status"] == STATUS_FAILED
    assert entry["error_kind"] == "bot"
    assert entry["playable"] is False


def test_the_status_endpoint_reports_progress(served) -> None:
    _, base, _ = served
    payload = json.loads(_request(base + "/api/status")[2])
    assert payload["active"][0]["percent"] == 42.0
    assert payload["queued"] == 1


# ----------------------------------------------------------------------
# Submitting
# ----------------------------------------------------------------------
def test_a_submitted_link_reaches_the_application(served) -> None:
    from clipster.app import SUBMIT_STARTED, SubmitResult

    app, base, _ = served
    app.result = SubmitResult(SUBMIT_STARTED, url="canonical")
    status, _, body = _request(base + "/api/submit", method="POST",
                               body={"url": "https://youtu.be/x", "format": "mp3"})
    assert status == 202
    assert app.submitted == [("https://youtu.be/x", "mp3", False)]
    assert json.loads(body)["accepted"] is True


def test_the_force_flag_is_passed_through(served) -> None:
    from clipster.app import SUBMIT_STARTED, SubmitResult

    app, base, _ = served
    app.result = SubmitResult(SUBMIT_STARTED)
    _request(base + "/api/submit", method="POST",
             body={"url": "u", "format": "mp4", "force": True})
    assert app.submitted[0][2] is True


@pytest.mark.parametrize("state,expected", [
    ("invalid", 400),
    ("format", 400),
    ("running", 409),
    ("waiting", 409),
    ("full", 503),
    ("closing", 503),
    ("exists", 200),
    ("started", 202),
    ("queued", 202),
])
def test_every_outcome_gets_its_own_status_code(served, state: str, expected: int) -> None:
    """The phone has to be able to tell "already there" from "refused"."""
    from clipster.app import SubmitResult

    app, base, _ = served
    app.result = SubmitResult(state)
    status, _, _ = _request(base + "/api/submit", method="POST",
                            body={"url": "u", "format": "mp3"})
    assert status == expected


def test_an_existing_download_comes_back_with_its_entry(served, tmp_path: Path) -> None:
    from clipster.app import SUBMIT_EXISTS, SubmitResult

    app, base, _ = served
    target = tmp_path / "song.mp3"
    target.write_bytes(b"abc")
    entry = HistoryEntry(name="song.mp3", path=str(target), url="u", media_format="mp3")
    app.entries.append(entry)
    app.result = SubmitResult(SUBMIT_EXISTS, url="u", entry_id=entry.identifier())

    status, _, body = _request(base + "/api/submit", method="POST",
                               body={"url": "u", "format": "mp3"})
    assert status == 200
    payload = json.loads(body)
    assert payload["entry"]["name"] == "song.mp3", "the phone can play it straight away"


@pytest.mark.parametrize("body", [None, {}, {"url": "u"}])
def test_a_useless_body_is_refused(served, body) -> None:
    from clipster.app import SUBMIT_FORMAT, SubmitResult

    app, base, _ = served
    app.result = SubmitResult(SUBMIT_FORMAT)
    status, _, _ = _request(base + "/api/submit", method="POST",
                            body=body if body is not None else {})
    assert status == 400


def test_a_body_that_is_not_json_is_refused(served) -> None:
    _, base, _ = served
    request = urllib.request.Request(base + "/api/submit", data=b"not json", method="POST")
    request.add_header(webserver.TOKEN_HEADER, TOKEN)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    assert status == 400


def test_an_oversized_body_is_refused(served) -> None:
    """A submission carries a URL; anything huge is not one."""
    _, base, _ = served
    status, _, _ = _request(base + "/api/submit", method="POST",
                            body={"url": "u" * (webserver.MAX_BODY + 100), "format": "mp3"})
    assert status == 400


def test_a_shutting_down_program_answers_503(served) -> None:
    app, base, _ = served
    app.raise_on_submit = True
    status, _, body = _request(base + "/api/submit", method="POST",
                               body={"url": "u", "format": "mp3"})
    assert status == 503
    assert json.loads(body)["accepted"] is False


def test_submitting_to_the_wrong_path_is_a_404(served) -> None:
    _, base, _ = served
    status, _, _ = _request(base + "/api/whatever", method="POST", body={"url": "u"})
    assert status == 404


# ----------------------------------------------------------------------
# Media files and Range
# ----------------------------------------------------------------------
@pytest.fixture()
def media(served, tmp_path: Path):
    """Put one playable file in the history and return its URL."""
    app, base, _ = served
    target = tmp_path / "song.mp3"
    target.write_bytes(bytes(range(256)))
    entry = HistoryEntry(name="song.mp3", path=str(target), url="u", media_format="mp3")
    app.entries.append(entry)
    return app, "{0}/media/{1}".format(base, entry.identifier()), target


def test_a_whole_file_is_served(media) -> None:
    _, url, target = media
    status, headers, body = _request(url)
    assert status == 200
    assert body == target.read_bytes()
    assert headers["Accept-Ranges"] == "bytes"


def test_a_range_is_answered_with_206(media) -> None:
    """Safari plays nothing without this."""
    _, url, target = media
    status, headers, body = _request(url, headers={"Range": "bytes=10-19"})
    assert status == 206
    assert body == target.read_bytes()[10:20]
    assert headers["Content-Range"] == "bytes 10-19/256"
    assert headers["Content-Length"] == "10"


def test_an_open_ended_range_runs_to_the_end(media) -> None:
    _, url, target = media
    status, headers, body = _request(url, headers={"Range": "bytes=200-"})
    assert status == 206
    assert body == target.read_bytes()[200:]
    assert headers["Content-Range"] == "bytes 200-255/256"


def test_a_suffix_range_returns_the_tail(media) -> None:
    _, url, target = media
    status, _, body = _request(url, headers={"Range": "bytes=-8"})
    assert status == 206
    assert body == target.read_bytes()[-8:]


def test_a_range_beyond_the_end_is_clamped(media) -> None:
    _, url, target = media
    status, headers, body = _request(url, headers={"Range": "bytes=250-9999"})
    assert status == 206
    assert body == target.read_bytes()[250:]
    assert headers["Content-Range"] == "bytes 250-255/256"


@pytest.mark.parametrize("header", ["", "items=0-1", "bytes=abc", "bytes=500-600",
                                    "bytes=20-10", "bytes=-0", "bytes"])
def test_an_unusable_range_falls_back_to_the_whole_file(media, header: str) -> None:
    _, url, target = media
    status, _, body = _request(url, headers={"Range": header} if header else None)
    assert status == 200
    assert body == target.read_bytes()


def test_several_ranges_at_once_yield_the_whole_file(media) -> None:
    _, url, target = media
    status, _, body = _request(url, headers={"Range": "bytes=0-9,20-29"})
    assert status == 200
    assert body == target.read_bytes()


def test_a_head_request_reports_the_size_without_the_body(media) -> None:
    _, url, target = media
    status, headers, body = _request(url, method="HEAD")
    assert status == 200
    assert headers["Content-Length"] == str(len(target.read_bytes()))
    assert body == b""


def test_the_file_name_travels_with_the_file(media) -> None:
    _, url, _ = media
    _, headers, _ = _request(url)
    assert "song.mp3" in headers.get("Content-Disposition", "")


def test_an_unknown_id_is_a_404(served) -> None:
    _, base, _ = served
    status, _, _ = _request(base + "/media/deadbeefdeadbeef")
    assert status == 404


def test_a_vanished_file_is_a_404(served, tmp_path: Path) -> None:
    app, base, _ = served
    entry = HistoryEntry(name="gone.mp3", path=str(tmp_path / "gone.mp3"),
                         url="u", media_format="mp3")
    app.entries.append(entry)
    status, _, _ = _request("{0}/media/{1}".format(base, entry.identifier()))
    assert status == 404


# ----------------------------------------------------------------------
# Deleting
# ----------------------------------------------------------------------
def test_a_download_can_be_deleted_from_the_phone(media) -> None:
    app, url, _ = media
    status, _, body = _request(url.replace("/media/", "/api/downloads/"), method="DELETE")
    assert status == 200
    assert json.loads(body)["deleted"] is True
    assert app.deleted, "the application was asked to delete it"


def test_deleting_an_unknown_id_is_a_404(served) -> None:
    _, base, _ = served
    status, _, _ = _request(base + "/api/downloads/nope", method="DELETE")
    assert status == 404


def test_a_failed_deletion_is_reported(media) -> None:
    app, url, _ = media
    app.delete_result = False
    status, _, body = _request(url.replace("/media/", "/api/downloads/"), method="DELETE")
    assert status == 500
    assert json.loads(body)["deleted"] is False


def test_deleting_needs_the_token(media) -> None:
    app, url, _ = media
    status, _, _ = _request(url.replace("/media/", "/api/downloads/"),
                            method="DELETE", token=None)
    assert status == 401
    assert not app.deleted


# ----------------------------------------------------------------------
# The server itself
# ----------------------------------------------------------------------
def test_the_port_is_reported_after_binding_zero(served) -> None:
    _, _, server = served
    assert server.port > 0
    assert server.running


def test_stopping_releases_the_port(web_dir: Path) -> None:
    server = webserver.RemoteServer(RemoteApi(FakeApp()), token=TOKEN, port=0, web_root=web_dir)
    assert server.start()
    port = server.port
    server.stop()
    assert not server.running
    again = webserver.RemoteServer(RemoteApi(FakeApp()), token=TOKEN, bind="127.0.0.1",
                                   port=port, web_root=web_dir)
    assert again.start(), "the port was not released"
    again.stop()


def test_a_taken_port_does_not_take_the_program_down(web_dir: Path) -> None:
    first = webserver.RemoteServer(RemoteApi(FakeApp()), token=TOKEN, bind="127.0.0.1",
                                   port=0, web_root=web_dir)
    assert first.start()
    try:
        second = webserver.RemoteServer(RemoteApi(FakeApp()), token=TOKEN, bind="127.0.0.1",
                                        port=first.port, web_root=web_dir)
        assert second.start() is False
        assert not second.running
    finally:
        first.stop()


def test_stopping_a_server_that_never_ran_is_harmless(web_dir: Path) -> None:
    webserver.RemoteServer(RemoteApi(FakeApp()), token=TOKEN, web_root=web_dir).stop()


def test_the_default_bind_stays_on_this_machine() -> None:
    """Reaching it from a phone has to be a deliberate decision."""
    from clipster.config import Config

    assert Config().remote_bind == "127.0.0.1"
    assert Config().remote_enabled is False


# ----------------------------------------------------------------------
# The interface that actually ships
# ----------------------------------------------------------------------
@pytest.fixture()
def real_web():
    """Serve the real clipster/web directory."""
    app = FakeApp()
    server = webserver.RemoteServer(RemoteApi(app), token=TOKEN, bind="127.0.0.1", port=0)
    assert server.start()
    try:
        yield app, "http://127.0.0.1:{0}".format(server.port)
    finally:
        server.stop()


@pytest.mark.parametrize("path,fragment", [
    ("/", b"YouTube Clipster"),
    ("/style.css", b"--accent"),
    ("/app.js", b"api/submit"),
    ("/sw.js", b"clipster-shell"),
    ("/manifest.webmanifest", b"share_target"),
    ("/icon.png", b"PNG"),
])
def test_every_shipped_file_is_reachable(real_web, path: str, fragment: bytes) -> None:
    _, base = real_web
    status, _, body = _request(base + path)
    assert status == 200, path
    assert fragment in body, path


def test_the_manifest_is_valid_json_and_installable(real_web) -> None:
    """Without name, icons and start_url Android refuses to install it."""
    _, base = real_web
    status, headers, body = _request(base + "/manifest.webmanifest")
    assert status == 200
    assert "json" in headers["Content-Type"]
    manifest = json.loads(body)
    assert manifest["name"] and manifest["start_url"] and manifest["icons"]
    assert manifest["display"] == "standalone"


def test_the_share_target_takes_a_shared_link(real_web) -> None:
    """This is what puts Clipster into the Android share sheet."""
    _, base = real_web
    manifest = json.loads(_request(base + "/manifest.webmanifest")[2])
    target = manifest["share_target"]
    assert target["action"] == "/"
    assert set(target["params"]) >= {"text", "url"}


def test_the_javascript_never_stores_the_token(real_web) -> None:
    """It rides on the cookie; a copy in storage would only be one more place to leak from."""
    _, base = real_web
    body = _request(base + "/app.js")[2].decode("utf-8")
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_the_service_worker_never_caches_the_api(real_web) -> None:
    """A cached list would show yesterday's downloads."""
    _, base = real_web
    body = _request(base + "/sw.js")[2].decode("utf-8")
    assert "/api/" in body and "/media/" in body
    assert body.index("const live") < body.index("respondWith")


def test_the_colours_match_the_desktop_theme(real_web) -> None:
    from clipster.theme import PALETTE

    _, base = real_web
    body = _request(base + "/style.css")[2].decode("utf-8")
    for colour in (PALETTE.base, PALETTE.panel, PALETTE.accent, PALETTE.border):
        assert colour in body, colour


def test_the_manifest_link_asks_for_credentials(real_web) -> None:
    """A manifest is fetched without cookies by default.

    Without ``crossorigin="use-credentials"`` the token-protected server answers
    401, the browser sees no manifest, and the page silently stops being
    installable - which also removes it from the Android share sheet.
    """
    _, base = real_web
    page = _request(base + "/")[2].decode("utf-8")
    link = [line for line in page.splitlines() if 'rel="manifest"' in line]
    assert link, "the manifest is not linked at all"
    assert 'crossorigin="use-credentials"' in link[0]


def test_the_manifest_is_reachable_with_the_cookie_alone(real_web) -> None:
    """Which is all the browser will send once the attribute is in place."""
    _, base = real_web
    status, _, _ = _request(base + "/manifest.webmanifest", token=None,
                            headers={"Cookie": "{0}={1}".format(webserver.TOKEN_COOKIE, TOKEN)})
    assert status == 200


def test_the_page_declares_itself_for_the_iphone_home_screen(real_web) -> None:
    _, base = real_web
    page = _request(base + "/")[2].decode("utf-8")
    assert "apple-touch-icon" in page
    assert "apple-mobile-web-app-capable" in page


def test_a_post_may_carry_the_token_in_the_url(served) -> None:
    """The iPhone Shortcuts app can send a URL and a body, but no header."""
    from clipster.app import SUBMIT_STARTED, SubmitResult

    app, base, _ = served
    app.result = SubmitResult(SUBMIT_STARTED, url="canonical")
    status, _, body = _request(base + "/api/submit?token=" + TOKEN, method="POST", token=None,
                               body={"url": "https://youtu.be/x", "format": "mp3"})
    assert status == 202
    assert json.loads(body)["accepted"] is True
    assert app.submitted == [("https://youtu.be/x", "mp3", False)]


def test_a_post_with_a_wrong_url_token_is_refused(served) -> None:
    app, base, _ = served
    status, _, _ = _request(base + "/api/submit?token=nope", method="POST", token=None,
                            body={"url": "u", "format": "mp3"})
    assert status == 401
    assert not app.submitted


def test_a_delete_may_carry_the_token_in_the_url(media) -> None:
    app, url, _ = media
    target = url.replace("/media/", "/api/downloads/") + "?token=" + TOKEN
    status, _, _ = _request(target, method="DELETE", token=None)
    assert status == 200
    assert app.deleted


# ----------------------------------------------------------------------
# The address handed to the phone
# ----------------------------------------------------------------------
def test_a_local_bind_yields_a_local_url() -> None:
    """A network address here would look inviting and then refuse to connect."""
    for bind in webserver.LOOPBACK_ADDRESSES:
        url = webserver.phone_url(bind, 8733, "tok")
        assert url == "http://127.0.0.1:8733/?token=tok", bind


def test_a_wide_bind_yields_the_network_url() -> None:
    url = webserver.phone_url("0.0.0.0", 8733, "tok")
    if not url:
        pytest.skip("this machine has no route to a network")
    assert url.endswith(":8733/?token=tok")
    assert "0.0.0.0" not in url, "a phone cannot dial the bind address"


def test_without_a_token_there_is_no_url() -> None:
    """Handing out an address that cannot authenticate only causes confusion."""
    assert webserver.phone_url("0.0.0.0", 8733, "") == ""


# ----------------------------------------------------------------------
# Content types, and why they are not guessed
# ----------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("index.html", "text/html; charset=utf-8"),
    ("style.css", "text/css; charset=utf-8"),
    ("app.js", "application/javascript; charset=utf-8"),
    ("manifest.webmanifest", "application/manifest+json; charset=utf-8"),
    ("icon.png", "image/png"),
    ("song.mp3", "audio/mpeg"),
    ("clip.MP4", "video/mp4"),
    ("something.bin", "application/octet-stream"),
    ("noextension", "application/octet-stream"),
])
def test_the_content_type_comes_from_a_fixed_table(name: str, expected: str) -> None:
    assert webserver.content_type(name) == expected


def test_every_format_the_program_produces_has_a_type() -> None:
    for suffix in (".mp3", ".mp4", ".m4a", ".webm"):
        assert webserver.content_type("x" + suffix) != webserver.DEFAULT_CONTENT_TYPE, suffix


def test_the_types_are_never_guessed_at_request_time() -> None:
    """mimetypes builds its table lazily and is not thread safe.

    Several threads racing inside ``mimetypes.init()`` can abort the process,
    which would take the downloader down with the web server.
    """
    # The call, not the comment that explains why it is not used.
    source = Path(webserver.__file__).read_text(encoding="utf-8")
    assert "mimetypes.guess_type(" not in source
    assert "import mimetypes" not in source


def test_many_parallel_requests_are_served(real_web) -> None:
    """What a browser does: page, style, script, manifest and icon at once."""
    import threading

    _, base = real_web
    paths = ["/", "/style.css", "/app.js", "/manifest.webmanifest", "/icon.png",
             "/sw.js", "/api/status", "/api/downloads"] * 4
    results: List[int] = []
    lock = threading.Lock()

    def fetch(path: str) -> None:
        status, _, _ = _request(base + path)
        with lock:
            results.append(status)

    threads = [threading.Thread(target=fetch, args=(path,)) for path in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == len(paths), "a request was lost"
    assert set(results) == {200}, sorted(set(results))


# ----------------------------------------------------------------------
# Values a user can put into config.json by hand
# ----------------------------------------------------------------------
@pytest.mark.parametrize("port,usable", [
    (0, True), (1, True), (8733, True), (65535, True),
    (65536, False), (99999, False), (-1, False), ("abc", False), (None, False),
])
def test_only_a_real_port_is_accepted(port, usable: bool) -> None:
    assert webserver.valid_port(port) is usable


def test_an_out_of_range_port_does_not_take_the_program_down(web_dir: Path) -> None:
    """socket.bind raises OverflowError there, which is not an OSError.

    An uncaught one escapes start_remote and ends the whole program at startup -
    from a single typo in a hand-edited configuration.
    """
    for port in (99999, -1, 70000):
        server = webserver.RemoteServer(RemoteApi(FakeApp()), token=TOKEN,
                                       bind="127.0.0.1", port=port, web_root=web_dir)
        assert server.start() is False, port
        assert not server.running


@pytest.mark.parametrize("bind", ["not-an-ip", "999.999.999.999", ""])
def test_an_unusable_bind_address_is_survived(web_dir: Path, bind: str) -> None:
    server = webserver.RemoteServer(RemoteApi(FakeApp()), token=TOKEN, bind=bind,
                                    port=0, web_root=web_dir)
    started = server.start()
    if started:  # an empty bind is legal and means "every interface"
        server.stop()


@pytest.mark.parametrize("token", ["a&b=c", "a/b?c#d", "a b c", "ümlaut", "plus+sign", "%41"])
def test_a_hand_edited_token_survives_the_address(token: str) -> None:
    """Unencoded, "&" would cut the address short and the phone would get a 401."""
    from urllib.parse import parse_qs, urlparse

    url = webserver.phone_url("0.0.0.0", 8733, token)
    if not url:
        pytest.skip("this machine has no route to a network")
    assert parse_qs(urlparse(url).query)["token"] == [token]


def test_a_token_with_special_characters_still_authenticates(web_dir: Path) -> None:
    """The whole point of encoding it: the server has to accept it back."""
    from urllib.parse import quote

    token = "a&b=c/d#e f"
    server = webserver.RemoteServer(RemoteApi(FakeApp()), token=token, bind="127.0.0.1",
                                    port=0, web_root=web_dir)
    assert server.start()
    try:
        base = "http://127.0.0.1:{0}".format(server.port)
        status, _, _ = _request("{0}/?token={1}".format(base, quote(token, safe="")), token=None)
        assert status == 200
        status, _, _ = _request(base + "/api/downloads", token=token)
        assert status == 200
    finally:
        server.stop()


# ----------------------------------------------------------------------
# Streaming over HTTP
# ----------------------------------------------------------------------
def test_the_streaming_state_is_served(served) -> None:
    _, base, _ = served
    status, headers, raw = _request(base + "/api/discover")
    assert status == 200
    payload = json.loads(raw)
    assert payload["tracks"][0]["title"] == "Song One"
    assert payload["playing"] is True
    assert payload["can_seek"] is True


def test_the_streaming_state_needs_the_token(served) -> None:
    _, base, _ = served
    assert _request(base + "/api/discover", token=None)[0] == 401


@pytest.mark.parametrize("command", ["toggle", "next", "previous", "like", "dislike",
                                     "download", "stop", "refresh", "extend"])
def test_a_command_is_passed_on(served, command: str) -> None:
    app, base, _ = served
    status, _, raw = _request(base + "/api/discover", method="POST",
                              body={"command": command})
    assert status == 200
    assert app.commands[-1][0] == command
    assert json.loads(raw)["ok"] is True


def test_a_queue_position_and_a_seek_are_passed_on(served) -> None:
    app, base, _ = served
    _request(base + "/api/discover", method="POST", body={"command": "play", "index": 2})
    assert app.commands[-1] == ("play", 2, 0.0)
    _request(base + "/api/discover", method="POST", body={"command": "seek", "seconds": 30.5})
    assert app.commands[-1] == ("seek", -1, 30.5)


@pytest.mark.parametrize("index,seconds", [("abc", "x"), (None, None), ([], {}), ("NaN", "NaN")])
def test_unusable_numbers_fall_back_instead_of_crashing(served, index, seconds) -> None:
    app, base, _ = served
    status, _, _ = _request(base + "/api/discover", method="POST",
                            body={"command": "play", "index": index, "seconds": seconds})
    assert status == 200
    assert app.commands[-1] == ("play", -1, 0.0)


def test_an_unknown_command_is_a_400(served) -> None:
    _, base, _ = served
    status, _, raw = _request(base + "/api/discover", method="POST", body={"command": "nope"})
    assert status == 400
    assert json.loads(raw)["ok"] is False


def test_missing_streaming_terms_answer_403(served) -> None:
    """403, not 400: the phone cannot fix this by retrying - a person has to."""
    app, base, _ = served
    app.terms_missing = True
    status, _, raw = _request(base + "/api/discover", method="POST", body={"command": "refresh"})
    assert status == 403
    assert json.loads(raw)["error"] == "terms_required"


def test_a_shutting_down_program_answers_503_for_streaming(served) -> None:
    app, base, _ = served
    app.raise_on_submit = True
    status, _, _ = _request(base + "/api/discover", method="POST", body={"command": "toggle"})
    assert status == 503


def test_the_interface_offers_both_views(real_web) -> None:
    _, base = real_web
    page = _request(base + "/")[2].decode("utf-8")
    assert 'id="tab-downloads"' in page
    assert 'id="tab-streaming"' in page
    assert 'id="view-streaming"' in page


def test_the_streaming_view_has_transport_controls(real_web) -> None:
    _, base = real_web
    page = _request(base + "/")[2].decode("utf-8")
    for control in ("stream-toggle", "stream-next", "stream-previous", "stream-like",
                    "stream-dislike", "stream-download", "stream-refresh", "queue"):
        assert 'id="{0}"'.format(control) in page, control


def test_the_script_talks_to_the_streaming_endpoint(real_web) -> None:
    _, base = real_web
    script = _request(base + "/app.js")[2].decode("utf-8")
    assert "/api/discover" in script
    assert "terms_required" in script, "the terms refusal has to be explained to the user"


def test_hiding_a_view_really_hides_it(real_web) -> None:
    """The browser's own [hidden] rule loses against any author display rule.

    "main { display: flex }" kept the hidden view on screen, so both views were
    stacked on top of each other - visible only in a screenshot.
    """
    import re

    _, base = real_web
    css = _request(base + "/style.css")[2].decode("utf-8")
    # Comments first: the one explaining this rule mentions "[hidden]" too.
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    match = re.search(r"\[hidden\]\s*\{([^}]*)\}", without_comments)
    assert match is not None, "no [hidden] rule at all"
    assert "display: none" in match.group(1)
    assert "!important" in match.group(1), "an author display rule would win otherwise"


# ----------------------------------------------------------------------
# Volume, search and the audio relay over HTTP
# ----------------------------------------------------------------------
def test_the_volume_is_part_of_the_state(served) -> None:
    _, base, _ = served
    payload = json.loads(_request(base + "/api/discover")[2])
    assert payload["volume"] == 42
    assert payload["volume_controllable"] is True
    assert payload["search_delay_ms"] == 1500


def test_a_volume_command_carries_its_value(served) -> None:
    app, base, _ = served
    _request(base + "/api/discover", method="POST", body={"command": "volume", "seconds": 35})
    assert app.commands[-1] == ("volume", -1, 35.0)


def test_a_search_is_passed_on(served) -> None:
    app, base, _ = served
    status, _, raw = _request(base + "/api/discover/search", method="POST",
                              body={"query": "beatles"})
    assert status == 200
    assert app.searches == ["beatles"]
    assert json.loads(raw)["results"][0]["title"] == "Hit for beatles"


def test_a_search_needs_the_token(served) -> None:
    app, base, _ = served
    assert _request(base + "/api/discover/search", method="POST", token=None,
                    body={"query": "x"})[0] == 401
    assert app.searches == []


def test_a_search_without_the_terms_is_403(served) -> None:
    app, base, _ = served
    app.terms_missing = True
    status, _, raw = _request(base + "/api/discover/search", method="POST", body={"query": "x"})
    assert status == 403
    assert json.loads(raw)["error"] == "terms_required"


def test_picking_a_result_queues_it(served) -> None:
    app, base, _ = served
    status, _, raw = _request(base + "/api/discover/queue", method="POST",
                              body={"video_id": "bbbbbbbbbbb", "title": "Hit", "play": True})
    assert status == 200
    assert app.enqueued[-1] == ("bbbbbbbbbbb", "Hit", True)
    assert json.loads(raw)["ok"] is True


def test_queueing_can_skip_the_playing(served) -> None:
    app, base, _ = served
    _request(base + "/api/discover/queue", method="POST",
             body={"video_id": "bbbbbbbbbbb", "title": "Hit", "play": False})
    assert app.enqueued[-1][2] is False


def test_a_nonsense_track_is_a_400(served) -> None:
    _, base, _ = served
    status, _, _ = _request(base + "/api/discover/queue", method="POST",
                            body={"video_id": "short"})
    assert status == 400


def test_queueing_needs_the_token(served) -> None:
    app, base, _ = served
    assert _request(base + "/api/discover/queue", method="POST", token=None,
                    body={"video_id": "bbbbbbbbbbb"})[0] == 401
    assert app.enqueued == []


def test_an_unresolvable_track_is_a_404(served) -> None:
    _, base, _ = served
    assert _request(base + "/stream/zzzzzzzzzzz")[0] == 404


def test_the_relay_needs_the_token(served) -> None:
    _, base, _ = served
    assert _request(base + "/stream/aaaaaaaaaaa", token=None)[0] == 401


def test_the_relay_passes_the_stream_through(served, tmp_path: Path) -> None:
    """A second server stands in for YouTube, so nothing leaves this machine."""
    app, base, _ = served
    upstream_dir = tmp_path / "upstream"
    upstream_dir.mkdir()
    payload = bytes(range(256)) * 8
    (upstream_dir / "audio.m4a").write_bytes(payload)

    class Upstream(FakeApp):
        pass

    upstream = webserver.RemoteServer(RemoteApi(Upstream()), token="up", bind="127.0.0.1",
                                      port=0, web_root=upstream_dir)
    assert upstream.start()
    try:
        app.audio_url = "http://127.0.0.1:{0}/audio.m4a?token=up".format(upstream.port)
        status, headers, body = _request(base + "/stream/aaaaaaaaaaa")
        assert status == 200, body[:200]
        assert body == payload
        assert headers.get("Accept-Ranges") == "bytes"

        status, headers, body = _request(base + "/stream/aaaaaaaaaaa",
                                        headers={"Range": "bytes=10-19"})
        assert status == 206, "Safari plays nothing without a 206"
        assert body == payload[10:20]
        assert headers.get("Content-Range", "").endswith("/2048")
    finally:
        upstream.stop()


def test_the_interface_offers_a_search_box_and_a_target(real_web) -> None:
    _, base = real_web
    page = _request(base + "/")[2].decode("utf-8")
    assert 'id="search"' in page
    assert 'name="target"' in page
    assert 'id="volume"' in page


def test_the_script_debounces_the_search(real_web) -> None:
    """Searching on every letter would ask the PC seven times for one word."""
    _, base = real_web
    script = _request(base + "/app.js")[2].decode("utf-8")
    assert "clearTimeout" in script and "setTimeout" in script
    assert "/api/discover/search" in script
    assert "search_delay_ms" in script, "the PC's setting has to win over the default"


def test_the_relay_makes_its_own_206_when_the_source_ignores_the_range(served, tmp_path: Path) -> None:
    """Safari plays nothing without a 206, whatever the source answers.

    The stand-in here serves static files and ignores Range on purpose - so this
    proves the range is honoured by the relay itself.
    """
    app, base, _ = served
    upstream_dir = tmp_path / "no-range"
    upstream_dir.mkdir()
    payload = bytes(range(256)) * 4
    (upstream_dir / "audio.m4a").write_bytes(payload)
    upstream = webserver.RemoteServer(RemoteApi(FakeApp()), token="up", bind="127.0.0.1",
                                     port=0, web_root=upstream_dir)
    assert upstream.start()
    try:
        app.audio_url = "http://127.0.0.1:{0}/audio.m4a?token=up".format(upstream.port)
        status, headers, body = _request(base + "/stream/aaaaaaaaaaa",
                                        headers={"Range": "bytes=100-199"})
        assert status == 206
        assert body == payload[100:200]
        assert headers["Content-Range"] == "bytes 100-199/{0}".format(len(payload))
        assert headers["Content-Length"] == "100"

        # An open-ended range has to work too.
        status, _, body = _request(base + "/stream/aaaaaaaaaaa",
                                   headers={"Range": "bytes=900-"})
        assert status == 206
        assert body == payload[900:]
    finally:
        upstream.stop()


def test_a_relay_without_a_range_stays_a_200(served, tmp_path: Path) -> None:
    app, base, _ = served
    upstream_dir = tmp_path / "plain"
    upstream_dir.mkdir()
    (upstream_dir / "audio.m4a").write_bytes(b"abcdef")
    upstream = webserver.RemoteServer(RemoteApi(FakeApp()), token="up", bind="127.0.0.1",
                                     port=0, web_root=upstream_dir)
    assert upstream.start()
    try:
        app.audio_url = "http://127.0.0.1:{0}/audio.m4a?token=up".format(upstream.port)
        status, headers, body = _request(base + "/stream/aaaaaaaaaaa")
        assert status == 200
        assert body == b"abcdef"
        assert headers["Accept-Ranges"] == "bytes"
    finally:
        upstream.stop()


# ----------------------------------------------------------------------
# The queue in the web interface
# ----------------------------------------------------------------------
def test_the_queue_is_a_scrollable_box(real_web) -> None:
    _, base = real_web
    css = _request(base + "/style.css")[2].decode("utf-8")
    assert "#queue" in css
    block = css.split("#queue", 1)[1].split("}", 1)[0]
    assert "max-height" in block
    assert "overflow-y: auto" in block


def test_the_queue_follows_the_playing_track(real_web) -> None:
    _, base = real_web
    script = _request(base + "/app.js")[2].decode("utf-8")
    assert "centreQueue" in script
    assert "getBoundingClientRect" in script, "offsetTop is relative to the wrong box"
    assert "centredOn" in script, "it must only scroll when the track changed"


def test_a_row_carries_its_video_id(real_web) -> None:
    """Needed to find the playing row again after the queue was rebuilt."""
    _, base = real_web
    script = _request(base + "/app.js")[2].decode("utf-8")
    assert "dataset.video" in script
    assert "data-video" in script


def test_the_results_can_be_folded_away(real_web) -> None:
    _, base = real_web
    page = _request(base + "/")[2].decode("utf-8")
    assert 'id="results-toggle"' in page
    assert 'aria-controls="results"' in page
    script = _request(base + "/app.js")[2].decode("utf-8")
    assert "showResults" in script
    assert "aria-expanded" in script


def test_a_result_lists_its_length(real_web) -> None:
    """The duration comes back from a flat search, so it is shown."""
    _, base = real_web
    script = _request(base + "/app.js")[2].decode("utf-8")
    results = script.split("function resultRow", 1)[1].split("\nfunction ", 1)[0]
    assert "formatDuration(found.duration)" in results
    assert "found.uploader" in results


def test_playback_on_the_device_starts_before_the_round_trip(real_web) -> None:
    """A phone only permits playback while the tap is still live.

    Awaiting the PC first loses that permission, and playing on the device then
    silently does nothing at all.
    """
    _, base = real_web
    script = _request(base + "/app.js")[2].decode("utf-8")
    body = script.split("async function pick", 1)[1].split("\nfunction ", 1)[0]
    play_at = body.index("playHere(found.video_id)")
    first_await = body.index("await api(")
    assert play_at < first_await, "playback is started after an await"
