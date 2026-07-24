"""Dynamic command sample.

Expected scan result: decision=needs_human_review,
rule_ids contains PROC001_PROCESS_EXEC (dynamic command) or
PARSE001_UNCERTAIN.

The script reads a command from stdin and executes it; the guard cannot
statically know what will run, so it falls back to review.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    user_command = sys.argv[1] if len(sys.argv) > 1 else "echo hello"
    return subprocess.run(user_command, shell=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
