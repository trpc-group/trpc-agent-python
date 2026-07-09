"""Print a compact summary for a unified diff."""

from __future__ import annotations

import sys


def main() -> int:
    text = sys.stdin.read()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            text = fh.read()
    files = [line for line in text.splitlines() if line.startswith("+++ b/")]
    hunks = [line for line in text.splitlines() if line.startswith("@@ ")]
    additions = [line for line in text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    print(f"files={len(files)} hunks={len(hunks)} additions={len(additions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
