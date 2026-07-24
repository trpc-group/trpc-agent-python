#!/usr/bin/env python3
"""Test fixture checker that fails during real sandbox execution."""

import sys


def main() -> int:
    sys.stderr.write("intentional fixture sandbox failure\n")
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
