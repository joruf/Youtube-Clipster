"""The HTTP transport of the phone interface.

A small ``ThreadingHTTPServer`` on a daemon thread, started from
:meth:`clipster.app.ClipsterApp.run` when ``remote_enabled`` is switched on - it
is off by default, and bound to this machine only until somebody deliberately
changes ``remote_bind``.  Only the standard library is used, so the phone
interface adds no dependency to the program.

Every decision lives in :mod:`clipster.webapi`; this module does four things:

* check the shared token on every single request,
* serve the files of ``clipster/web`` from a table built at startup, so no path
  out of a request ever reaches the file system,
* answer ``Range`` requests, because Safari refuses to play media without them,
* keep the server's own logging out of ``sys.stderr``, which does not exist when
  the program was started by ``run.bat`` through ``pythonw.exe``.
"""

from __future__ import annotations

import hmac
import json
import secrets
import socket
import threading
import urllib.request
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

from . import paths
from .logging_setup import get_logger

log = get_logger(__name__)

#: Name of the cookie that carries the token once it was accepted.
TOKEN_COOKIE = "clipster_token"
#: Header the web interface sends the token in.
TOKEN_HEADER = "X-Clipster-Token"
#: Query parameter used by the first request, which comes from a QR code.
TOKEN_QUERY = "token"

#: Content type per file extension, for everything this program serves.
#:
#: Deliberately not ``mimetypes.guess_type``: that builds its table lazily on the
#: first call and is not thread safe.  A browser fetches the page, the style, the
#: script and the icon at once, each on its own server thread, and racing inside
#: ``mimetypes.init()`` can abort the whole process - taking the downloader with
#: it.  The set of types here is closed: these are the files this program ships
#: and the formats it produces.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/vnd.microsoft.icon",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
}

#: Sent for anything not in :data:`CONTENT_TYPES`; a browser then offers to save.
DEFAULT_CONTENT_TYPE = "application/octet-stream"

#: Largest request body accepted; a submission carries a URL, nothing more.
MAX_BODY = 8 * 1024
#: Chunk size while streaming a file to the phone.
_CHUNK = 64 * 1024

#: Bind addresses that keep the phone interface on this machine.
LOOPBACK_ADDRESSES = ("127.0.0.1", "localhost", "::1")


def _as_int(value: Any, fallback: int) -> int:
    """Read an integer out of a JSON body without trusting it.

    :param value: Whatever arrived.
    :param fallback: Used when it is not a number.
    :return: The integer.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_float(value: Any, fallback: float) -> float:
    """Read a float out of a JSON body without trusting it.

    :param value: Whatever arrived.
    :param fallback: Used when it is not a number.
    :return: The float.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback          # reject NaN


def valid_port(port: int) -> bool:
    """Return whether ``port`` can be bound at all.

    ``socket.bind`` raises :class:`OverflowError` outside this range - which is
    *not* an ``OSError`` - so an out-of-range value from a hand-edited
    configuration would escape the usual error handling and take the whole
    program down on startup.

    :param port: The port from the configuration.
    :return: ``True`` for 0 (pick a free one) and for every real port.
    """
    try:
        number = int(port)
    except (TypeError, ValueError):
        return False
    return 0 <= number <= 65535


def content_type(name: str) -> str:
    """Return the content type for a file name.

    :param name: The file name, with or without a path.
    :return: The type from :data:`CONTENT_TYPES`, or the default.
    """
    suffix = Path(name).suffix.lower()
    return CONTENT_TYPES.get(suffix, DEFAULT_CONTENT_TYPE)


def new_token() -> str:
    """Return a fresh shared secret for the phone interface.

    :return: A URL-safe token.
    """
    return secrets.token_urlsafe(24)


def static_files(root: Optional[Path] = None) -> Dict[str, Path]:
    """Return the servable files of the web directory, keyed by URL path.

    Built once at startup, so a request never turns into a file system lookup: a
    path that is not in this table simply does not exist, which is a stronger
    guarantee than trying to sanitise the request.

    :param root: The directory to read; defaults to ``clipster/web``.
    :return: ``{"/index.html": Path, ...}``
    """
    base = root or paths.web_root()
    table: Dict[str, Path] = {}
    if not base.is_dir():
        log.warning("The web directory %s is missing - the phone interface has no files.", base)
        return table
    for item in sorted(base.rglob("*")):
        if item.is_file():
            table["/" + item.relative_to(base).as_posix()] = item
    return table


def parse_range(header: str, size: int) -> Optional[Tuple[int, int]]:
    """Interpret a ``Range`` header for a file of ``size`` bytes.

    Only the single range form is supported, which is all a media element asks
    for.  Safari plays neither audio nor video unless the server answers this
    with ``206``, so on the iPhone it is not optional.

    :param header: The raw header value, for example ``bytes=200-1023``.
    :param size: The size of the file.
    :return: The inclusive ``(start, end)``, or ``None`` when unusable.
    """
    if not header or not header.strip().lower().startswith("bytes=") or size <= 0:
        return None
    spec = header.split("=", 1)[1].strip()
    if "," in spec:
        # Several ranges at once: answering with the whole file is allowed.
        return None
    start_text, separator, end_text = spec.partition("-")
    if not separator:
        return None
    try:
        if not start_text:
            # "bytes=-500" asks for the last 500 bytes.
            length = int(end_text)
            if length <= 0:
                return None
            return max(0, size - length), size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def phone_url(bind: str, port: int, token: str) -> str:
    """Return the address to open on a phone, token included.

    Shared by the running program and ``tools/phone_link.py`` so both can never
    disagree about what to type into the phone.  While the server is bound to
    loopback the network address is *not* returned: it would look inviting and
    then refuse every connection.

    :param bind: The configured bind address.
    :param port: The port the server listens on.
    :param token: The shared secret.
    :return: The full URL, or an empty string when it cannot be determined.
    """
    if bind in LOOPBACK_ADDRESSES:
        base = "http://127.0.0.1:{0}/".format(port)
    else:
        base = local_address(port)
    if not base or not token:
        return ""
    # Encoded, not interpolated: a hand-edited token containing "&" or "#" would
    # otherwise cut the address short, and the phone would be refused with no
    # visible reason.
    return "{0}?token={1}".format(base, quote(str(token), safe=""))


def local_host() -> str:
    """Return the address of the interface facing the local network.

    :return: An IPv4 address, or an empty string when it cannot be determined.
    """
    try:
        # Nothing is sent: connecting a UDP socket only picks the route, which
        # reveals which local interface faces the network.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return str(probe.getsockname()[0])
    except OSError:
        log.debug("The local network address could not be determined")
        return ""


def local_address(port: int) -> str:
    """Return the address a phone on the same network can use.

    :param port: The port the server listens on.
    :return: ``http://<ip>:<port>/``, or an empty string when unknown.
    """
    host = local_host()
    if not host:
        return ""
    return "http://{0}:{1}/".format(host, port)


class RemoteServer:
    """Owns the HTTP server thread of the phone interface."""

    def __init__(self, api: Any, *, token: str, bind: str = "127.0.0.1", port: int = 8733,
                 web_root: Optional[Path] = None) -> None:
        """
        :param api: The :class:`clipster.webapi.RemoteApi` to serve.
        :param token: The shared secret every request has to carry.
        :param bind: Interface to listen on; the default stays on this machine.
        :param port: TCP port to listen on; ``0`` picks a free one.
        :param web_root: Directory of the static files, for tests.
        """
        self._api = api
        self._token = token
        self._bind = bind
        self._port = port
        self._static = static_files(web_root)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    @property
    def port(self) -> int:
        """Return the port actually in use, which differs when ``0`` was asked for."""
        if self._server is None:
            return self._port
        return int(self._server.server_address[1])

    @property
    def running(self) -> bool:
        """Return ``True`` while the server thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Bind the port and serve in the background.

        A port that is already taken must not take the program down: the phone
        interface is a convenience, the downloader is the program.

        :return: ``True`` when the server is listening.
        """
        if not self._token:
            log.error("Remote control needs a token and was not started.")
            return False
        if not valid_port(self._port):
            log.error("The remote control port %r is not between 0 and 65535 - "
                      "check \"remote_port\" in the configuration.", self._port)
            return False
        handler = _make_handler(self._api, self._token, self._static)
        try:
            self._server = ThreadingHTTPServer((self._bind, self._port), handler)
        except (OSError, OverflowError, ValueError) as exc:
            # OverflowError and ValueError are not OSError, and an uncaught one
            # here would end the program instead of only the phone interface.
            log.error("Remote control cannot listen on %s:%s (%s).", self._bind, self._port, exc)
            self._server = None
            return False
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="clipster-remote",
            daemon=True,
        )
        self._thread.start()
        log.info("Remote control is listening on http://%s:%s/", self._bind, self.port)
        return True

    def stop(self) -> None:
        """Stop serving and release the port."""
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # pragma: no cover - shutting down must never raise
                log.debug("Remote control did not shut down cleanly", exc_info=True)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)


def _make_handler(api: Any, token: str, static: Dict[str, Path]) -> type:
    """Build a request handler class bound to one API, token and file table.

    :param api: The :class:`clipster.webapi.RemoteApi` to call.
    :param token: The expected shared secret.
    :param static: The servable files from :func:`static_files`.
    :return: A :class:`BaseHTTPRequestHandler` subclass.
    """

    class Handler(BaseHTTPRequestHandler):
        """One request of the phone interface."""

        server_version = "YoutubeClipster"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # --------------------------------------------------------------
        # Plumbing
        # --------------------------------------------------------------
        def log_message(self, format: str, *args: Any) -> None:
            """Route the server's own log line into the application logger.

            The default writes to ``sys.stderr``, which is ``None`` under
            ``pythonw.exe`` - that would fail on every single request.
            """
            log.debug("remote %s - %s", self.address_string(), format % args)

        def _tokens_offered(self) -> Tuple[str, ...]:
            """Collect every token this request carries."""
            found = []
            header = self.headers.get(TOKEN_HEADER)
            if header:
                found.append(header)
            query = parse_qs(urlparse(self.path).query).get(TOKEN_QUERY)
            if query:
                found.append(query[0])
            raw_cookie = self.headers.get("Cookie")
            if raw_cookie:
                jar = SimpleCookie()
                try:
                    jar.load(raw_cookie)
                except Exception:  # pragma: no cover - malformed cookie header
                    jar = SimpleCookie()
                if TOKEN_COOKIE in jar:
                    found.append(jar[TOKEN_COOKIE].value)
            return tuple(found)

        def _authorised(self) -> bool:
            """Return whether this request carries the shared secret."""
            # compare_digest, not ==: how long a plain comparison takes reveals
            # how much of the token was guessed correctly.
            return any(hmac.compare_digest(offered, token) for offered in self._tokens_offered())

        def _from_query(self) -> bool:
            """Return whether the token arrived in the URL, as from a QR code."""
            return bool(parse_qs(urlparse(self.path).query).get(TOKEN_QUERY))

        def _cookie_header(self) -> Dict[str, str]:
            """Remember the token when it came from the URL.

            Media elements cannot send a header of their own, so the cookie is
            what lets ``<audio src="/media/...">`` work afterwards.
            """
            if not self._from_query():
                return {}
            return {
                "Set-Cookie": "{0}={1}; Path=/; Max-Age=31536000; SameSite=Strict".format(
                    TOKEN_COOKIE, token
                )
            }

        def _send(self, status: int, body: bytes, content_type: str,
                  extra: Optional[Dict[str, str]] = None) -> None:
            """Write one complete response."""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_json(self, status: int, payload: Dict[str, Any],
                       extra: Optional[Dict[str, str]] = None) -> None:
            """Write a JSON response."""
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", extra)

        def _answer(self, status: int, payload: Dict[str, Any]) -> None:
            """Send an API result."""
            self._send_json(status, payload, self._cookie_header())

        def _deny(self) -> None:
            """Answer a request that did not carry the token."""
            # The path matters: a browser fetches some things without cookies,
            # and without it in the log that is very hard to work out.
            log.warning("Remote control refused %s %s from %s.",
                        self.command, urlparse(self.path).path, self.address_string())
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorised"})

        def _not_found(self) -> None:
            """Answer an unknown route."""
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _read_json(self) -> Optional[Dict[str, Any]]:
            """Read and decode the request body, or ``None`` when unusable."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > MAX_BODY:
                return None
            try:
                decoded = json.loads(self.rfile.read(length).decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                return None
            return decoded if isinstance(decoded, dict) else None

        # --------------------------------------------------------------
        # Routing
        # --------------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 - the name is fixed by the base class
            """Serve the interface, the API reads and the media files."""
            if not self._authorised():
                self._deny()
                return
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._serve_static("/index.html")
            elif path == "/api/downloads":
                self._answer(*api.downloads())
            elif path == "/api/status":
                self._answer(*api.status())
            elif path == "/api/discover":
                self._answer(*api.discover())
            elif path.startswith("/media/"):
                self._serve_media(path[len("/media/"):])
            elif path.startswith("/stream/"):
                self._relay_stream(path[len("/stream/"):])
            elif path in static:
                self._serve_static(path)
            else:
                self._not_found()

        def do_HEAD(self) -> None:  # noqa: N802
            """Answer like the matching GET, without a body."""
            self.do_GET()

        def do_POST(self) -> None:  # noqa: N802
            """Take a new link."""
            if not self._authorised():
                self._deny()
                return
            route = urlparse(self.path).path
            if route not in ("/api/submit", "/api/discover", "/api/discover/search",
                             "/api/discover/queue"):
                self._not_found()
                return
            payload = self._read_json()
            if payload is None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "expected a JSON object"})
                return
            if route == "/api/discover/search":
                self._answer(*api.discover_search(str(payload.get("query") or "")))
                return
            if route == "/api/discover/queue":
                self._answer(*api.discover_enqueue(payload))
                return
            if route == "/api/discover":
                self._answer(*api.discover_command(
                    str(payload.get("command") or ""),
                    _as_int(payload.get("index"), -1),
                    _as_float(payload.get("seconds"), 0.0),
                ))
                return
            self._answer(*api.submit(
                str(payload.get("url") or ""),
                str(payload.get("format") or ""),
                bool(payload.get("force")),
            ))

        def do_DELETE(self) -> None:  # noqa: N802
            """Delete a download and its file."""
            if not self._authorised():
                self._deny()
                return
            path = urlparse(self.path).path
            prefix = "/api/downloads/"
            if not path.startswith(prefix):
                self._not_found()
                return
            self._answer(*api.delete(path[len(prefix):]))

        # --------------------------------------------------------------
        # Files
        # --------------------------------------------------------------
        def _serve_static(self, key: str) -> None:
            """Send one file of the web interface."""
            target = static.get(key)
            if target is None:
                self._not_found()
                return
            try:
                body = target.read_bytes()
            except OSError as exc:
                log.error("Could not read %s: %s", target, exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "unreadable"})
                return
            extra = self._cookie_header()
            if key == "/index.html":
                extra["Cache-Control"] = "no-store"
            self._send(HTTPStatus.OK, body, content_type(target.name), extra)

        def _serve_media(self, entry_id: str) -> None:
            """Stream a downloaded file, honouring a ``Range`` request."""
            target = api.media(entry_id)
            if target is None:
                self._not_found()
                return
            try:
                size = target.stat().st_size
            except OSError as exc:
                log.error("Could not read %s: %s", target, exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "unreadable"})
                return

            window = parse_range(self.headers.get("Range") or "", size)
            start, end = window if window else (0, max(0, size - 1))
            length = (end - start + 1) if size else 0

            self.send_response(HTTPStatus.PARTIAL_CONTENT if window else HTTPStatus.OK)
            self.send_header("Content-Type", content_type(target.name))
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if window:
                self.send_header("Content-Range", "bytes {0}-{1}/{2}".format(start, end, size))
            # Decides what name the phone offers when the file is saved.
            self.send_header("Content-Disposition",
                             "inline; filename*=UTF-8''{0}".format(quote(target.name, safe="")))
            self.end_headers()
            if self.command != "HEAD":
                self._stream(target, start, length)

        def _relay_range(self, answer: Any, wanted: str) -> Tuple[int, Optional[Tuple[int, int, int]]]:
            """Work out what to do when the source ignored a ``Range``.

            YouTube honours ranges, but a source that does not would hand the
            device a ``200`` - and Safari then plays nothing at all. In that case
            the range is served from here: the prefix is read and thrown away and
            the answer becomes a proper ``206``.

            :param answer: The open upstream response.
            :param wanted: The ``Range`` header the device sent, if any.
            :return: ``(bytes to skip, (start, end, total) or None)``.
            """
            if not wanted or answer.status != 200:
                return 0, None
            try:
                total = int(answer.headers.get("Content-Length") or 0)
            except ValueError:
                return 0, None
            window = parse_range(wanted, total)
            if window is None:
                return 0, None
            start, end = window
            log.debug("The source ignored the range; serving %s-%s of %s here", start, end, total)
            return start, (start, end, total)

        def _relay_stream(self, video_id: str) -> None:
            """Pass a Streaming track through to the device that asked for it.

            Relayed rather than redirected: YouTube's own URLs are bound to the
            address that resolved them and expire, and a redirect would also take
            the request out of this origin, where the token protects it.

            :param video_id: The video id from the queue.
            :return: None
            """
            upstream = api.discover_audio(video_id)
            if not upstream:
                self._not_found()
                return
            headers = {"User-Agent": "YoutubeClipster"}
            wanted = self.headers.get("Range")
            if wanted:
                # Passed straight through: seeking on the device has to work, and
                # Safari will not start playing without a 206 at all.
                headers["Range"] = wanted
            request = urllib.request.Request(upstream, headers=headers)
            try:
                answer = urllib.request.urlopen(request, timeout=20)
            except Exception as exc:  # pragma: no cover - needs the network
                log.warning("The audio stream could not be relayed: %s", exc)
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "upstream"})
                return
            try:
                skip, window = self._relay_range(answer, wanted)
                status = HTTPStatus.PARTIAL_CONTENT if window else answer.status
                self.send_response(status)
                self.send_header("Content-Type",
                                 answer.headers.get("Content-Type") or DEFAULT_CONTENT_TYPE)
                if window:
                    start, end, total = window
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Content-Range",
                                     "bytes {0}-{1}/{2}".format(start, end, total))
                else:
                    length = answer.headers.get("Content-Length")
                    if length:
                        self.send_header("Content-Length", length)
                    passed = answer.headers.get("Content-Range")
                    if passed:
                        self.send_header("Content-Range", passed)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if self.command == "HEAD":
                    return
                while skip > 0:
                    block = answer.read(min(_CHUNK, skip))
                    if not block:
                        break
                    skip -= len(block)
                remaining = (window[1] - window[0] + 1) if window else None
                while remaining is None or remaining > 0:
                    block = answer.read(_CHUNK if remaining is None
                                        else min(_CHUNK, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    if remaining is not None:
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                # The device skipped, paused or locked its screen.
                log.debug("The device closed the connection while receiving %s", video_id)
            except OSError as exc:  # pragma: no cover - network error mid-stream
                log.debug("The audio relay stopped: %s", exc)
            finally:
                answer.close()

        def _stream(self, target: Path, start: int, length: int) -> None:
            """Copy ``length`` bytes of ``target`` to the client."""
            remaining = length
            try:
                with target.open("rb") as handle:
                    handle.seek(start)
                    while remaining > 0:
                        block = handle.read(min(_CHUNK, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                # A phone that seeks, or locks its screen, drops the connection.
                # That is normal and no reason for an error in the log.
                log.debug("The phone closed the connection while receiving %s", target.name)
            except OSError as exc:  # pragma: no cover - disk error mid-stream
                log.error("Could not send %s: %s", target, exc)

    return Handler
