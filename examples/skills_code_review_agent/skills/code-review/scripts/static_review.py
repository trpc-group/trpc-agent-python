"""Sandbox static-review probe.

The main pipeline owns structured findings. This script provides independent
evidence that can run in a workspace runtime.
"""

from __future__ import annotations

import json
import sys

PATTERNS = {
    "shell_true": "shell=True",
    "dynamic_eval": "eval(",
    "dynamic_exec": "exec(",
    "tls_verify_false": "verify=False",
    "force_failure": "force_sandbox_failure",
}


def main() -> int:
    if "--force-failure" in sys.argv:
        print("forced failure requested", file=sys.stderr)
        return 2
    text = sys.stdin.read()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            text = fh.read()
    hits = [{"pattern": name, "count": text.count(token)} for name, token in PATTERNS.items() if token in text]
    print(json.dumps({"static_hits": hits}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
