# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Allowlisted sandbox script that summarizes a unified diff from stdin."""

from __future__ import annotations

import json
import sys


def main() -> int:
    """Read diff text from stdin and emit a small JSON summary."""
    diff_text = sys.stdin.read()
    files = sum(1 for line in diff_text.splitlines() if line.startswith("diff --git "))
    hunks = sum(1 for line in diff_text.splitlines() if line.startswith("@@ "))
    added = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    print(
        json.dumps(
            {
                "script": "diff_summary",
                "files": files,
                "hunks": hunks,
                "added_lines": added,
                "removed_lines": removed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
