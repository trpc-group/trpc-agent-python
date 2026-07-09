"""Detect whether a diff contains source and test changes."""

from __future__ import annotations

import json
import sys


def main() -> int:
    text = sys.stdin.read()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            text = fh.read()
    files = [line[6:].strip() for line in text.splitlines() if line.startswith("+++ b/")]
    source = [
        path for path in files
        if path.endswith(".py") and not path.startswith("tests/") and not path.rsplit("/", 1)[-1].startswith("test_")
    ]
    tests = [path for path in files if path.startswith("tests/") or path.rsplit("/", 1)[-1].startswith("test_")]
    print(
        json.dumps(
            {
                "source_files": source,
                "test_files": tests,
                "missing_tests": bool(source and not tests),
            },
            sort_keys=True,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
