#!/usr/bin/env python3
"""Ally: an always-on, OS-level accessibility agent.

    python run.py            start Ally (restarts itself if it crashes)
    python run.py doctor     check your setup
    python run.py --help     all commands
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.version_info < (3, 11):
    sys.exit("Ally needs Python 3.11 or newer (you have %d.%d)." % sys.version_info[:2])

from ally.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
