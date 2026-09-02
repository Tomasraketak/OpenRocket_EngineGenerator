"""Spouštěč aplikace (dvojklik ve Windows spustí bez konzolového okna)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine_generator.gui import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
