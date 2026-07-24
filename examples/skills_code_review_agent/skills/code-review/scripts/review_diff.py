#!/usr/bin/env python3
"""Run the bundled review agent from a source checkout."""

from __future__ import annotations

import sys
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EXAMPLE_ROOT))

from cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
