# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI: parse a unified diff file and print a JSON summary to stdout."""
import json
import sys

from diffparse import parse_unified_diff, summarize


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: parse_diff.py <diff-file>"}))
        return 2
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        files = parse_unified_diff(fh.read())
    print(json.dumps({"summary": summarize(files), "files": files}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
