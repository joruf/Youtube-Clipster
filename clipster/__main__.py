"""Entry point for ``python -m clipster``.

Assumes the environment is already prepared (yt-dlp and FFmpeg available).
Use ``run.py`` when the dependency check should run as well.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
