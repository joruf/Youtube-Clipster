#!/usr/bin/env python3
"""Show the address of the phone interface, as text and as a QR code.

Typing a 32 character token into a phone by hand is no fun, so this prints a QR
code straight into the terminal: run it, hold the phone in front of the screen,
done.

    python3 tools/phone_link.py                 # URL and QR code in the terminal
    python3 tools/phone_link.py --png link.png  # additionally write a PNG
    python3 tools/phone_link.py --url           # only the URL, for piping

Nothing leaves this machine: the QR code is generated locally, because the token
is a password and has no business being sent to a web service.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of this tool."""
    parser = argparse.ArgumentParser(
        prog="phone_link.py",
        description="Print the address of the phone interface and a QR code for it.",
    )
    parser.add_argument("--config", metavar="FILE", help="path to an alternative config.json")
    parser.add_argument("--png", metavar="FILE", help="also write the QR code as a PNG")
    parser.add_argument("--url", action="store_true", help="print only the URL, nothing else")
    return parser


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
    # invert=True prints dark modules as spaces, which is what a phone camera
    # expects on the usual light-on-dark terminal.
    code.print_ascii(invert=True)
    return True


def _write_png(url: str, target: Path) -> bool:
    """Write a QR code for ``url`` to ``target``.

    :param url: The address to encode.
    :param target: The PNG file to write.
    :return: ``True`` when the file was written.
    """
    try:
        import qrcode
    except ImportError:
        return False
    image = qrcode.make(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(target))
    return True


def main(argv: Optional[list] = None) -> int:
    """Read the configuration and report the address.

    :param argv: Argument list; defaults to ``sys.argv[1:]``.
    :return: The process exit code.
    """
    args = build_parser().parse_args(argv)

    from clipster import paths
    from clipster.config import Config
    from clipster.webserver import LOOPBACK_ADDRESSES, new_token, phone_url

    target = Path(args.config).expanduser() if args.config else paths.config_file()
    config = Config.load(target)

    if not config.remote_token:
        # The program generates one at startup; doing it here too means the link
        # can be produced before the first start, and both agree afterwards.
        config.remote_token = new_token()
        config.save()
        if not args.url:
            print("A token was generated and written to {0}.".format(config.path))

    url = phone_url(config.remote_bind, config.remote_port, config.remote_token)
    if not url:
        print("The address could not be determined - this machine has no network route.",
              file=sys.stderr)
        return 1

    if args.url:
        print(url)
        return 0

    print()
    print(url)
    print()

    if not _print_qr(url):
        print("For a QR code, install the optional package:  pip install qrcode")
    if args.png and not _write_png(url, Path(args.png).expanduser()):
        print("The PNG needs the optional package:  pip install qrcode", file=sys.stderr)
    elif args.png:
        print("QR code written to {0}".format(args.png))

    if not config.remote_enabled:
        print()
        print('Note: "remote_enabled" is false - the program does not serve this yet.')
    if config.remote_bind in LOOPBACK_ADDRESSES:
        print()
        print('Note: "remote_bind" is "{0}", so only this machine can reach it.'
              .format(config.remote_bind))
        print('      Set it to "0.0.0.0" in {0} to let a phone in.'.format(config.path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
