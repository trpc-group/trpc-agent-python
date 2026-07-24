# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Missing-test checker: source .py files changed but no test files changed."""
import sys

from checklib import emit, finding, load_files


def _is_test_path(path):
    name = path.rsplit("/", 1)[-1]
    return (path.startswith("tests/") or "/tests/" in path
            or name.startswith("test_") or name.endswith("_test.py"))


def main():
    files = load_files(sys.argv)
    changed_paths = [f["path"] or f["old_path"] for f in files]
    tests_changed = any(_is_test_path(p) for p in changed_paths)
    findings = []
    if not tests_changed:
        for f in files:
            path = f["path"] or f["old_path"]
            if not path.endswith(".py") or _is_test_path(path):
                continue
            if not f["added_lines"]:
                continue
            findings.append(finding(
                "medium", "missing_test", path, f["added_lines"][0]["line"],
                "Source change without accompanying test change",
                evidence="%d added line(s), no test files in this diff" % len(f["added_lines"]),
                recommendation="Add or update unit tests covering the changed behavior.",
                confidence=0.8))
    emit(findings)


if __name__ == "__main__":
    main()
