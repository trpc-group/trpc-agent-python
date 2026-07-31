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
    try:
        text = _read_diff_input(sys.argv[1:])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    hits = [{"pattern": name, "count": text.count(token)} for name, token in PATTERNS.items() if token in text]
    print(json.dumps({"static_hits": hits}, sort_keys=True))
    return 0


def _read_diff_input(args: list[str]) -> str:
    diff_paths = [arg for arg in args if not arg.startswith("--")]
    unknown_flags = [arg for arg in args if arg.startswith("--") and arg != "--force-failure"]
    if unknown_flags:
        raise ValueError(f"unsupported static_review flags: {', '.join(unknown_flags)}")
    if not diff_paths:
        return sys.stdin.read()
    if diff_paths != ["work/input.diff"]:
        raise ValueError("static_review only accepts the sandbox-provided work/input.diff path")
    with open(diff_paths[0], "r", encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    raise SystemExit(main())
