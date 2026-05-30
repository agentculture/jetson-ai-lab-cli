"""Entry point for ``python -m jlab``."""

from __future__ import annotations

import sys

from jlab.cli import main

if __name__ == "__main__":
    sys.exit(main())
