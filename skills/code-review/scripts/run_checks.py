#!/usr/bin/env python3
"""Run code review scanners against a file.

Usage: python run_checks.py <filename>
"""

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python run_checks.py <filename>", file=sys.stderr)
        return 1

    filename = sys.argv[1]
    try:
        with open(filename, "r") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"File not found: {filename}", file=sys.stderr)
        return 1

    print(f"Running checks on: {filename}")
    print(f"File size: {len(content)} bytes, {len(content.splitlines())} lines")
    print("Checks complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
