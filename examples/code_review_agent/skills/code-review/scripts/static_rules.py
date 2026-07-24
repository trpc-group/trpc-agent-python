# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Allowlisted sandbox script for deterministic static-rule smoke checks."""

from __future__ import annotations

import json
import re
import sys

_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]")
_EVAL_RE = re.compile(r"\b(eval|exec)\s*\(")


def main() -> int:
    """Read diff text from stdin and emit non-secret rule counters."""
    diff_text = sys.stdin.read()
    added_lines = [line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    result = {
        "script": "static_rules",
        "added_lines": len(added_lines),
        "secret_like_additions": sum(1 for line in added_lines if _SECRET_RE.search(line)),
        "dynamic_execution_additions": sum(1 for line in added_lines if _EVAL_RE.search(line)),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
