#!/usr/bin/env python3
"""Executable entrypoint for BaseApp."""

import os
import sys


# Ensure project root is importable when running from app/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from utils.baseutils import *
from utils.apputils import *


class App(Main):
    """Concrete application entrypoint."""

    def __init__(self, args=None):
        super().__init__(args=args or [])


def run(argv=None):
    """Run the app and return process exit code."""
    app = App((argv or sys.argv)[1:])
    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
