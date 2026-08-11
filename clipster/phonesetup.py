"""A guided setup that gets the phone interface onto a phone.

Started with ``run.py --phone-setup`` (or ``run.bat --phone-setup``), it walks
through everything by hand-holding instead of by documentation:

1. explains what will happen and asks whether to go ahead,
2. switches ``remote_enabled`` on and opens ``remote_bind`` to the network,
3. works out the address of this machine and checks that the port is free,
4. looks at the firewall and offers to open the port,
5. shows a QR code and **waits until the phone actually connects**,
6. explains the two steps that are left on the phone itself.

Step 5 is the point of the whole thing: it uses the real
:class:`clipster.webserver.RemoteServer` with a tiny pairing page, so the token,
the firewall and the network path are proven to work before the program is even
started - and the phone is left holding the token cookie, which pairs it for
good.

Everything is a console dialogue: no Tk, so it also works over SSH.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import APP_TITLE, paths
from .config import Config
from .logging_setup import get_logger
from .webserver import LOOPBACK_ADDRESSES, RemoteServer, local_host, new_token, phone_url

log = get_logger(__name__)

#: How long to wait for the phone before giving up, in seconds.
PAIRING_TIMEOUT = 300

#: The page the phone loads while pairing. Deliberately not the real interface:
#: nothing can be downloaded yet, and pretending otherwise would confuse.
_PAIRING_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>YouTube Clipster - paired</title>
<style>
body { margin:0; min-height:100vh; display:flex; align-items:center;
       justify-content:center; background:#15161a; color:#e9eaee;
       font:16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width:22rem; padding:2rem; text-align:center; }
h1 { font-size:1.3rem; margin:0 0 .75rem; }
p { color:#8b909c; margin:0 0 1rem; }
.mark { width:64px; height:64px; margin:0 auto 1.25rem; border-radius:50%;
        background:#e5322d; color:#fff; font-size:2rem; line-height:64px; }
.ok { color:#3fb950; font-weight:600; }
</style>
</head>
<body>
<main>
    <div class="mark">&#10003;</div>
    <h1>Connected</h1>
    <p>This phone can reach YouTube Clipster on your PC.</p>
    <p id="state">Registering&hellip;</p>
</main>
<script>
fetch("/api/status", {cache: "no-store"})
    .then(function (response) {
        var state = document.getElementById("state");
        if (response.ok) {
            state.className = "ok";
            state.textContent = "Paired. Go back to the PC.";
        } else {
            state.textContent = "The PC refused this device (status " + response.status + ").";
        }
    })
    .catch(function () {
        document.getElementById("state").textContent = "The PC could not be reached.";
    });
</script>
</body>
</html>
"""


class _PairingApi:
    """Answers just enough for the pairing page, and reports the visit."""

    def __init__(self) -> None:
        self.seen = threading.Event()
        self.visitors: List[str] = []

    def status(self, connection: str = "") -> Tuple[int, Dict[str, Any]]:
        """Record that a device got through and answer it.

        :param connection: What the device says about its connection; there is
            no playback to apply it to yet, so it is only noted.
        """
        del connection
        self.seen.set()
        return 200, {"active": [], "queued": 0, "parallel": 1, "pairing": True}

    def downloads(self) -> Tuple[int, Dict[str, Any]]:
        """No list exists during pairing."""
        return 200, {"downloads": []}

    def media(self, entry_id: str) -> Optional[Path]:
        """Nothing is playable during pairing."""
        return None

    def queue_media(self, position: str) -> Optional[Path]:
        """No queue exists during pairing."""
        return None

    def streaming_allowed(self) -> bool:
        """Nothing is streamed during pairing, so the question is moot."""
        return False

    def share_code(self, video_id: str) -> Tuple[int, str]:
        """No song can be shared before the program runs."""
        return 404, ""

    def scan(self, text: str) -> Tuple[int, Dict[str, Any]]:
        """Nothing can be queued during pairing."""
        return 503, {"ok": False, "error": "closing"}

    def submit(self, url: str, media_format: str, force: bool = False) -> Tuple[int, Dict[str, Any]]:
        """Refuse downloads: the program itself is not running yet."""
        return 503, {"state": "closing", "accepted": False}

    def delete(self, entry_id: str) -> Tuple[int, Dict[str, Any]]:
        """Nothing can be deleted during pairing."""
        return 404, {"deleted": False}


# ----------------------------------------------------------------------
# Console helpers
# ----------------------------------------------------------------------
def _say(text: str = "") -> None:
    """Write one line for the user."""
    print(text, flush=True)


def _step(number: int, total: int, title: str) -> None:
    """Announce one step of the wizard."""
    _say()
    _say("[{0}/{1}] {2}".format(number, total, title))
    _say("-" * 66)


def _ask(question: str, default: bool = True) -> bool:
    """Ask a yes/no question.

    :param question: The question, without the choices.
    :param default: What an empty answer means.
    :return: The answer.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input("{0} {1} ".format(question, suffix)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            _say()
            return False
        if not answer:
            return default
        if answer in ("y", "yes", "j", "ja"):
            return True
        if answer in ("n", "no", "nein"):
            return False


def _wait_for_enter(text: str) -> None:
    """Wait until the user presses Enter."""
    try:
        input(text)
    except (EOFError, KeyboardInterrupt):
        _say()


# ----------------------------------------------------------------------
# The individual checks
# ----------------------------------------------------------------------
def port_is_free(bind: str, port: int) -> bool:
    """Return whether the port can be bound right now.

    :param bind: The interface to try.
    :param port: The TCP port to try.
    :return: ``True`` when nothing is listening there.
    """
    family = socket.AF_INET6 if ":" in bind else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind, port))
        except OSError:
            return False
    return True


def firewall_hint(port: int) -> Tuple[str, str]:
    """Describe the firewall situation and the command that would open the port.

    Nothing is executed here - a privileged command is only ever run after the
    user has seen it and agreed.

    :param port: The port that has to be reachable.
    :return: ``(description, command)``; an empty command means nothing to do.
    """
    if paths.IS_WINDOWS:
        return ("Windows Firewall asks once whether the program may accept "
                "connections - allow it for private networks.",
                'netsh advfirewall firewall add rule name="YouTube Clipster" '
                'dir=in action=allow protocol=TCP localport={0}'.format(port))
    if shutil.which("ufw"):
        active = _ufw_is_active()
        if active is False:
            return ("ufw is installed but not active, so nothing is blocking the port.", "")
        if active is None:
            return ("ufw is installed; its status needs root, so it could not be read.",
                    "sudo ufw allow {0}/tcp".format(port))
        return ("ufw is active and will block the port until it is allowed.",
                "sudo ufw allow {0}/tcp".format(port))
    if shutil.which("firewall-cmd"):
        return ("firewalld is installed and may block the port.",
                "sudo firewall-cmd --add-port={0}/tcp --permanent && "
                "sudo firewall-cmd --reload".format(port))
    return ("No firewall was found that would need to be opened.", "")


def _ufw_is_active() -> Optional[bool]:
    """Return whether ufw is active, or ``None`` when it cannot be read."""
    try:
        finished = subprocess.run(["sudo", "-n", "ufw", "status"], capture_output=True,
                                  text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0:
        return None
    return "inactive" not in finished.stdout.lower()


def _print_qr(url: str) -> bool:
    """Draw a QR code for ``url`` into the terminal.

    :param url: The address to encode.
    :return: ``True`` when it could be drawn.
    """
    try:
        import qrcode
    except ImportError:
        return False
    code = qrcode.QRCode(border=2)
    code.add_data(url)
    code.print_ascii(invert=True)
    return True


# ----------------------------------------------------------------------
# The wizard
# ----------------------------------------------------------------------
def run(config: Optional[Config] = None) -> int:
    """Walk the user through getting the phone interface onto a phone.

    :param config: The configuration to change; loaded when omitted.
    :return: The process exit code.
    """
    settings = config if config is not None else Config.load()
    total = 6

    _say()
    _say("=" * 66)
    _say("  {0} - phone setup".format(APP_TITLE))
    _say("=" * 66)
    _say()
    _say("This gets your phone connected to this PC. The phone sends links,")
    _say("this PC downloads them. Nothing has to be installed on the phone.")
    _say()
    _say("What will happen:")
    _say('  * "remote_enabled" and "remote_bind" are changed in')
    _say("      {0}".format(settings.path))
    _say('  * a "remote_token" is generated, which your phone needs to log in')
    _say("  * the phone interface becomes reachable on your local network")
    _say("  * you scan a QR code and we verify the phone really got through")
    _say()
    _say("This does not open anything to the internet, and nothing is sent")
    _say("anywhere - the QR code is generated here on this machine.")
    _say()
    if not _ask("Continue?"):
        _say("Nothing was changed.")
        return 1

    # ---------------------------------------------------------------- 1
    _step(1, total, "Settings")
    changes = []
    if not settings.remote_enabled:
        changes.append('"remote_enabled": true')
    if settings.remote_bind in LOOPBACK_ADDRESSES:
        changes.append('"remote_bind": "0.0.0.0"')
    if not settings.remote_token:
        changes.append('"remote_token": <a new token>')

    if not changes:
        _say("Already set up - nothing to change.")
    else:
        _say("These will be written to {0}:".format(settings.path))
        for change in changes:
            _say("  * {0}".format(change))
        _say()
        _say('Note: "0.0.0.0" means every device on the networks this PC is on')
        _say("can reach the interface - which is what lets your phone in. The")
        _say("token is what keeps everybody else out.")
        _say()
        if not _ask("Write these settings?"):
            _say("Nothing was changed.")
            return 1
        settings.remote_enabled = True
        if settings.remote_bind in LOOPBACK_ADDRESSES:
            settings.remote_bind = "0.0.0.0"
        if not settings.remote_token:
            settings.remote_token = new_token()
        settings.save()
        _say("Written.")

    # ---------------------------------------------------------------- 2
    _step(2, total, "Network")
    host = local_host()
    if not host:
        _say("The address of this machine could not be determined. Is it")
        _say("connected to a network?")
        return 1
    # Only the host: the port that ends up in use is known once the server has
    # bound it, which is step 5 - naming it here would be a guess.
    _say("This machine: {0}".format(host))
    _say("Your phone has to be on the same Wi-Fi.")

    # ---------------------------------------------------------------- 3
    _step(3, total, "Port {0}".format(settings.remote_port))
    if port_is_free(settings.remote_bind, settings.remote_port):
        _say("Port {0} is free.".format(settings.remote_port))
    else:
        _say("Something is already listening on port {0}.".format(settings.remote_port))
        _say("If that is YouTube Clipster, it is already serving the interface -")
        _say("in that case quit it first, so this setup can test the connection.")
        _say('Otherwise pick a different "remote_port" in {0}.'.format(settings.path))
        if not _ask("Try anyway?", default=False):
            return 1

    # ---------------------------------------------------------------- 4
    _step(4, total, "Firewall")
    description, command = firewall_hint(settings.remote_port)
    _say(description)
    if command:
        _say()
        _say("The command that opens the port:")
        _say("  {0}".format(command))
        _say()
        if _ask("Run it now?", default=False):
            _run_privileged(command)
        else:
            _say("Skipped - run it yourself if the phone cannot connect.")

    # ---------------------------------------------------------------- 5
    _step(5, total, "Connect the phone")
    url = _pair(settings)
    if not url:
        return 1

    # ---------------------------------------------------------------- 6
    _step(6, total, "Two things left, on the phone")
    _say("1. Put it on the home screen")
    _say("     Android (Chrome): menu -> Add to home screen -> Install")
    _say("     iPhone (Safari):  share button -> Add to Home Screen")
    _say()
    _say("   On Android this is also what puts Clipster into the share menu.")
    _say()
    _say("2. Then share instead of copy")
    _say("     YouTube app -> Share -> Clipster -> the download starts here")
    _say()
    _say("   Shared links come down as MP3. For MP4, open Clipster on the")
    _say("   phone, paste the link and pick MP4.")
    _say()
    _say("=" * 66)
    _say("Done. Start {0} normally now:".format(APP_TITLE))
    _say("  {0}".format("run.bat" if paths.IS_WINDOWS else "python3 run.py"))
    _say()
    _say("Your phone already holds the token, so it can simply open")
    _say("  {0}".format(url.split("?")[0]))
    _say("=" * 66)
    return 0


def _run_privileged(command: str) -> None:
    """Run a firewall command the user has seen and agreed to.

    :param command: The exact command shown above.
    :return: None
    """
    _say("Running: {0}".format(command))
    try:
        finished = subprocess.run(command, shell=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        _say("That did not work ({0}). Run it yourself.".format(exc))
        return
    if finished.returncode == 0:
        _say("Port opened.")
    else:
        _say("The command failed (exit code {0}).".format(finished.returncode))
        if paths.IS_WINDOWS:
            _say("On Windows this needs an Administrator console.")


def _pair(settings: Config) -> str:
    """Show the QR code and wait until the phone gets through.

    :param settings: The active configuration.
    :return: The address the phone used, or an empty string on failure.
    """
    workspace = Path(tempfile.mkdtemp(prefix="clipster-pairing-"))
    (workspace / "index.html").write_text(_PAIRING_PAGE, encoding="utf-8")
    api = _PairingApi()
    server = RemoteServer(api, token=settings.remote_token, bind=settings.remote_bind,
                          port=settings.remote_port, web_root=workspace)
    if not server.start():
        _say("The interface could not listen on {0}:{1}.".format(
            settings.remote_bind, settings.remote_port))
        shutil.rmtree(workspace, ignore_errors=True)
        return ""

    # Built from the port the server really got, which differs from the
    # configured one whenever that was 0.
    url = phone_url(settings.remote_bind, server.port, settings.remote_token)
    if not url:
        _say("The address could not be built - is this machine on a network?")
        server.stop()
        shutil.rmtree(workspace, ignore_errors=True)
        return ""

    try:
        _say("Scan this with your phone's camera:")
        _say()
        if not _print_qr(url):
            _say("  (No QR code - the optional package is missing:")
            _say("   pip install qrcode)")
            _say()
        _say(url)
        _say()
        _say("Waiting for your phone... (Ctrl+C to skip)")
        try:
            reached = api.seen.wait(timeout=PAIRING_TIMEOUT)
        except KeyboardInterrupt:
            _say()
            _say("Skipped.")
            if _ask("Carry on without a verified connection?", default=False):
                return url
            return ""
        if not reached:
            _say()
            _say("No phone got through within {0} minutes.".format(PAIRING_TIMEOUT // 60))
            _say("The usual reasons:")
            _say("  * phone on a different Wi-Fi, or on a guest network")
            _say("  * the firewall is still blocking port {0}".format(settings.remote_port))
            _say("  * the address was mistyped - scanning the code avoids that")
            return ""
        _say()
        _say("Your phone reached this PC. Token, network and firewall all work.")
        return url
    finally:
        server.stop()
        shutil.rmtree(workspace, ignore_errors=True)
