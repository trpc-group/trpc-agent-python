# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared helpers for checker scripts."""
import json

from diffparse import parse_unified_diff


def load_files(argv):
    """Parse the diff file given as argv[1]; exit(2) on usage error."""
    if len(argv) < 2:
        print(json.dumps({"error": "usage: <checker> <diff-file>"}))
        raise SystemExit(2)
    with open(argv[1], encoding="utf-8", errors="replace") as fh:
        return parse_unified_diff(fh.read())


def finding(severity, category, file, line, title, evidence="", recommendation="", confidence=0.9):
    """Build one finding dict (source is always 'static' for scripts)."""
    return {
        "severity": severity,
        "category": category,
        "file": file,
        "line": line,
        "title": title,
        "evidence": evidence,
        "recommendation": recommendation,
        "confidence": confidence,
        "source": "static",
    }


def emit(findings):
    """Print the findings JSON contract to stdout."""
    print(json.dumps({"findings": findings}))


def iter_added_py_lines(files):
    """Yield (path, line_no, text) for added lines in Python files."""
    for f in files:
        path = f["path"] or f["old_path"]
        if not path.endswith(".py"):
            continue
        for added in f["added_lines"]:
            yield path, added["line"], added["text"]
