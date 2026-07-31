"""Detect whether a diff contains source and test changes."""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        text = _read_diff_input(sys.argv[1:])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
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


def _read_diff_input(args: list[str]) -> str:
    if not args:
        return sys.stdin.read()
    if args != ["work/input.diff"]:
        raise ValueError("test_probe only accepts the sandbox-provided work/input.diff path")
    with open(args[0], "r", encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    raise SystemExit(main())
