# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox entry: parse a raw unified diff into a normalized changeset JSON.

Usage (inside the code-review skill workspace)::

    python3 skills/code-review/scripts/parse_diff.py <raw.diff> <out.json>

Stdlib-only; safe to run in any sandbox runtime (local / container / cube).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.diffparse import build_diff_summary  # noqa: E402
from lib.diffparse import parse_unified_diff  # noqa: E402


def main(argv: list) -> int:
    if len(argv) != 3:
        print("usage: parse_diff.py <raw.diff> <out.json>", file=sys.stderr)
        return 2
    raw_path, out_path = argv[1], argv[2]
    with open(raw_path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    changeset = parse_unified_diff(text)
    payload = {"changeset": changeset, "summary": build_diff_summary(changeset)}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"parsed {payload['summary']['file_count']} file(s), "
          f"{payload['summary']['hunk_count']} hunk(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
